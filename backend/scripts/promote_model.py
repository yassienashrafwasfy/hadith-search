"""Pick the best Arabic encoder in an MLflow model registry, export it to ONNX and re-embed the corpus.

    python -m scripts.promote_model run   [--registered-model NAME] [--margin 0.01] [--dry-run]
    python -m scripts.promote_model prune [--keep 3] [--dry-run]

Normally run through `tools/promote_model.sh`, which also starts and promotes the new colour.

`run` does these steps and stops at the first that fails:

1. select: score every READY version of the registered model, and the live ONNX model, on the
   labelled pairs stored in MLflow (nDCG@10 of the cosine ranking, see `ndcg_at_k`). The best
   version must beat the live model by `--margin`.
2. export: turn it into ONNX with `scripts.export_onnx`, which refuses an export whose vectors
   differ from the PyTorch model (minimum cosine 0.9999).
3. embed: encode the whole corpus with the new ONNX model into a new table,
   `hadith_embeddings_<release>`. The live colour keeps reading `hadith_embeddings`.
4. stage: write `deploy/state/model.staged.env` (the two environment variables the new colour
   needs). Nothing in production changes until `tools/promote_model.sh run --promote`.

Settings come from the environment or the git-ignored `.env` (only the `MLFLOW_*` lines are read from it, and a variable that is already set wins): `MLFLOW_TRACKING_URI` (and
`MLFLOW_TRACKING_TOKEN` or the other MLflow auth variables), `DATABASE_URL`, `ARABIC_MODEL_DIR`
(the live model, the baseline). Exit codes: 0 done, 1 failed, 2 bad usage or settings, 3 no
version beats the live model, 4 the ONNX export did not match the original.

Eval pairs file (a JSON list, logged as an artifact of each version's run, `eval/pairs.json`):
`[{"query": "...", "text": "...", "label": 2, "hadith_id": 17}, ...]`. `label` is the relevance
(0 means not relevant, larger is better); `hadith_id` is optional and only for people. All
versions must have the same pairs file, or `--pairs-run-id` must name the one run to read it from.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import func, select

from scripts.arabic_encoder import EMBEDDING_DIM, MAX_LENGTH, encoding_text, load_encoder
from settings import ENV_FILE

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(BACKEND_DIR)
STATE_DIR = os.path.join(REPO_DIR, "deploy", "state")
DEFAULT_STAGE_DIR = os.path.join(BACKEND_DIR, "data", "onnx", "releases")
DEFAULT_CONTAINER_DIR = "/app/backend/data/onnx/releases"
DEFAULT_PAIRS_ARTIFACT = "eval/pairs.json"
DEFAULT_MARGIN = 0.01
DEFAULT_KEEP = 3
NDCG_K = 10
EMBED_CHUNK = 2000
EXIT_FAILED, EXIT_USAGE, EXIT_NO_WINNER, EXIT_PARITY = 1, 2, 3, 4

Encode = Callable[[list[str]], np.ndarray]


class PromotionError(Exception):
    """A step failed in a way the owner can read and fix; `code` is the exit code."""

    def __init__(self, message: str, code: int = EXIT_FAILED):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Pair:
    query: str
    text: str
    label: float


@dataclass(frozen=True)
class Candidate:
    version: str
    run_id: str | None
    score: float | None = None


def _log(message: str) -> None:
    print(f"{datetime.now(timezone.utc):%H:%M:%S} promote_model: {message}", flush=True)


# --- scoring (pure) -------------------------------------------------------------------------


def ndcg_at_k(labels_in_rank_order: list[float], k: int = NDCG_K) -> float:
    """Normalised discounted cumulative gain of one ranking; the gain of a result is its label.

    DCG = sum over the first k results of label / log2(rank + 1), ranks starting at 1. The ideal
    DCG is the same sum with the labels sorted best first. Returns 0.0 when no label is positive.
    """
    discounts = 1.0 / np.log2(np.arange(2, k + 2))

    def dcg(labels: list[float]) -> float:
        top = np.asarray(labels[:k], dtype=float)
        return float((top * discounts[: len(top)]).sum())

    ideal = dcg(sorted(labels_in_rank_order, reverse=True))
    return dcg(labels_in_rank_order) / ideal if ideal > 0 else 0.0


def score_pairs(pairs: list[Pair], encode: Encode) -> float:
    """Mean nDCG@10 over the queries: rank each query's texts by cosine, compare with the labels.

    Queries whose labels are all 0 are skipped (their nDCG is undefined). `encode` returns one
    vector per text, with at least EMBEDDING_DIM values; they are cut to EMBEDDING_DIM and made
    unit length here, the same as in production.
    """
    by_query: dict[str, list[Pair]] = defaultdict(list)
    for pair in pairs:
        by_query[pair.query].append(pair)
    queries = [q for q, rows in by_query.items() if any(r.label > 0 for r in rows)]
    if not queries:
        raise PromotionError("The eval pairs have no query with a positive label", EXIT_USAGE)
    queries_matrix = _unit(encode(queries))
    scores = []
    for query_vector, query in zip(queries_matrix, queries):
        rows = by_query[query]
        texts = _unit(encode([r.text for r in rows]))
        order = np.argsort(-(texts @ query_vector), kind="stable")
        scores.append(ndcg_at_k([rows[i].label for i in order]))
    return float(np.mean(scores))


def _unit(vectors: np.ndarray) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] < EMBEDDING_DIM:
        raise PromotionError(
            f"The model gives vectors of shape {matrix.shape}; at least {EMBEDDING_DIM} values "
            "per text are needed (production keeps the first 64)."
        )
    cut = matrix[:, :EMBEDDING_DIM]
    return cut / np.maximum(np.linalg.norm(cut, axis=1, keepdims=True), 1e-12)


def load_pairs(path: str) -> list[Pair]:
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        return [Pair(str(r["query"]), str(r["text"]), float(r["label"])) for r in rows]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PromotionError(
            f"The eval pairs file is not a JSON list of objects with query, text and label "
            f"({type(exc).__name__}: {exc})",
            EXIT_USAGE,
        ) from exc


def decide(best: float, baseline: float, margin: float) -> bool:
    """True when the best candidate beats the live model by at least `margin` (absolute)."""
    return best - baseline >= margin - 1e-12


def pick_best(candidates: list[Candidate]) -> Candidate:
    """Highest score; on a tie the newer version (versions are integers in the registry)."""
    return max(candidates, key=lambda c: (c.score, _version_number(c.version)))


def _version_number(version: str) -> int:
    return int(version) if version.isdigit() else -1


# --- encoders -------------------------------------------------------------------------------


def _cleaned(encode: Encode) -> Encode:
    """Apply the cleanup production applies to queries and hadiths before encoding."""
    return lambda texts: encode([encoding_text(t) for t in texts])


def _torch_encoder(model_dir: str) -> Encode:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_dir, device="cpu")
    model.max_seq_length = MAX_LENGTH
    return _cleaned(lambda texts: model.encode(texts, convert_to_numpy=True))


def _onnx_encoder(model_dir: str) -> Encode:
    return _cleaned(load_encoder(model_dir, threads=0).encode)


# --- MLflow ---------------------------------------------------------------------------------


def _load_mlflow_env() -> None:
    """Copy the MLFLOW_* lines of the repo's `.env` into the environment. Variables already set win."""
    from dotenv import dotenv_values

    for key, value in dotenv_values(ENV_FILE).items():
        if key.startswith("MLFLOW_") and value:
            os.environ.setdefault(key, value)


def _mlflow():
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    _load_mlflow_env()
    try:
        import mlflow
    except ImportError as exc:
        raise PromotionError(
            "mlflow is not installed. Run: uv pip install -r requirements-mlops.txt", EXIT_USAGE
        ) from exc
    if not os.environ.get("MLFLOW_TRACKING_URI"):
        raise PromotionError("Set MLFLOW_TRACKING_URI to your MLflow server.", EXIT_USAGE)
    return mlflow


def list_candidates(name: str, versions: list[str] | None, skip: str | None) -> list[Candidate]:
    """READY versions of the registered model, minus `skip` (the version already live)."""
    mlflow = _mlflow()
    if "'" in name:
        raise PromotionError("The registered model name must not contain a quote", EXIT_USAGE)
    found = mlflow.MlflowClient().search_model_versions(f"name='{name}'")
    ready = [v for v in found if str(getattr(v, "status", "READY")) == "READY"]
    chosen = [
        Candidate(str(v.version), v.run_id)
        for v in ready
        if (not versions or str(v.version) in versions) and str(v.version) != skip
    ]
    if not chosen:
        raise PromotionError(
            f"No READY version of {name!r} to consider (live version: {skip or 'unknown'}).",
            EXIT_NO_WINNER,
        )
    return chosen


def find_model_dir(root: str) -> str:
    """The sentence-transformers folder inside a downloaded registry version.

    A plain folder (logged as artifacts) has `modules.json` at its top; the MLflow
    sentence-transformers flavor keeps it in a subfolder. Falls back to the folder that has a
    Hugging Face `config.json`.
    """
    for marker in ("modules.json", "config.json"):
        for folder, _dirs, files in sorted(os.walk(root)):
            if marker in files:
                return folder
    raise PromotionError(f"No sentence-transformers model folder found under {root}")


def download_version(name: str, version: str, dest: str) -> str:
    target = os.path.join(dest, name.replace("/", "_"), f"v{version}")
    if not os.path.isdir(target):
        os.makedirs(target)
        _mlflow().artifacts.download_artifacts(
            artifact_uri=f"models:/{name}/{version}", dst_path=target
        )
    return find_model_dir(target)


def download_pairs(run_id: str | None, artifact: str, dest: str) -> str:
    if not run_id:
        raise PromotionError(
            "The version has no source run, so its eval pairs cannot be found. "
            "Use --pairs-run-id.",
            EXIT_USAGE,
        )
    try:
        return _mlflow().artifacts.download_artifacts(
            run_id=run_id, artifact_path=artifact, dst_path=dest
        )
    except Exception as exc:
        raise PromotionError(
            f"Run {run_id} has no artifact {artifact!r} (the eval pairs). Log the labelled pairs "
            f"there, or pass --pairs-artifact / --pairs-run-id ({type(exc).__name__})",
            EXIT_USAGE,
        ) from exc


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# --- state files ----------------------------------------------------------------------------


def _read_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep:
                    values[key] = value
    return values


def _write_env(path: str, values: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.writelines(f"{k}={v}\n" for k, v in values.items())
    os.replace(path + ".tmp", path)


def _promotion_file(stage_dir: str, release: str) -> str:
    return os.path.join(stage_dir, release, "promotion.json")


def _read_steps(stage_dir: str, release: str) -> dict:
    path = _promotion_file(stage_dir, release)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"steps": {}}


def _mark(stage_dir: str, release: str, step: str, **info) -> None:
    state = _read_steps(stage_dir, release)
    state["steps"][step] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds")} | info
    os.makedirs(os.path.join(stage_dir, release), exist_ok=True)
    with open(_promotion_file(stage_dir, release), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# --- steps ----------------------------------------------------------------------------------


def select_best(args, workdir: str) -> tuple[Candidate, float, float]:
    """Score the candidates and the live model; returns (best, its score, the live model's score)."""
    live_dir = args.live_model_dir
    if not os.path.isfile(os.path.join(live_dir, "model.onnx")):
        raise PromotionError(
            f"There is no live ONNX model in {live_dir} to compare with (set ARABIC_MODEL_DIR).",
            EXIT_USAGE,
        )
    live_version = _read_env(os.path.join(STATE_DIR, "model.env")).get("MODEL_VERSION")
    candidates = list_candidates(args.registered_model, args.versions, live_version)
    _log(f"{len(candidates)} candidate version(s): {', '.join(c.version for c in candidates)}")

    pair_files = {}
    for c in candidates:
        run_id = args.pairs_run_id or c.run_id
        pair_files[c.version] = download_pairs(
            run_id, args.pairs_artifact, os.path.join(workdir, "pairs", c.version)
        )
    hashes = {_sha256(path) for path in pair_files.values()}
    if len(hashes) > 1:
        raise PromotionError(
            "The versions have different eval pairs files, so their scores cannot be compared. "
            "Pass --pairs-run-id to read one pairs file for all of them.",
            EXIT_USAGE,
        )
    pairs = load_pairs(next(iter(pair_files.values())))
    _log(f"{len(pairs)} labelled pairs, metric nDCG@{NDCG_K}")

    scored = []
    for c in candidates:
        model_dir = download_version(args.registered_model, c.version, args.download_dir or workdir)
        score = score_pairs(pairs, _torch_encoder(model_dir))
        _log(f"  version {c.version}: {score:.4f}")
        scored.append(Candidate(c.version, c.run_id, score))
    baseline = score_pairs(pairs, _onnx_encoder(live_dir))
    _log(f"  live model ({live_dir}): {baseline:.4f}")
    best = pick_best(scored)
    return best, best.score, baseline


def export_step(args, best: Candidate, release: str) -> None:
    from scripts import export_onnx

    out = os.path.join(args.stage_dir, release)
    model_dir = download_version(
        args.registered_model, best.version, args.download_dir or args.stage_dir
    )
    _log(f"exporting version {best.version} to ONNX in {out}")
    try:
        info = export_onnx.export(
            out,
            source_dir=model_dir,
            source_name=f"{args.registered_model}/v{best.version}",
            source_revision=best.run_id,
        )
    except RuntimeError as exc:  # no opset passed the parity check
        raise PromotionError(f"ONNX export failed: {exc}", EXIT_PARITY) from exc
    _mark(args.stage_dir, release, "exported", min_parity_cosine=info["min_parity_cosine"])


def embed_step(args, best: Candidate, release: str) -> int:
    """Encode the whole corpus with the staged model into hadith_embeddings_<release>."""
    from database import get_sync_engine, get_sync_session, init_schema_sync
    from models import EmbeddingSet
    from models.embedding_sets import embedding_table
    from scripts.build_embeddings import _load_corpus, _passages
    from scripts.embedding_store import store_embeddings

    init_schema_sync()
    table = embedding_table(release)
    with get_sync_engine().begin() as conn:
        table.create(conn, checkfirst=True)
    df = _load_corpus()
    ids = df["id"].astype(int).tolist()
    texts = _passages(df)
    encoder = load_encoder(os.path.join(args.stage_dir, release), threads=0)
    for start in range(0, len(ids), EMBED_CHUNK):
        chunk = slice(start, start + EMBED_CHUNK)
        with get_sync_session() as session:
            store_embeddings(session, ids[chunk], encoder.encode(texts[chunk]), release)
        _log(f"embedded {min(start + EMBED_CHUNK, len(ids))} / {len(ids)}")
    with get_sync_session() as session:
        session.merge(
            EmbeddingSet(
                release=release,
                model_version=best.version,
                dim=EMBEDDING_DIM,
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        )
        session.commit()
        stored = session.execute(select(func.count()).select_from(table)).scalar_one()
    if stored != len(ids):
        raise PromotionError(f"{stored} vectors stored for {len(ids)} hadiths")
    _mark(args.stage_dir, release, "embedded", hadiths=len(ids))
    return len(ids)


def stage_step(args, best: Candidate, release: str) -> str:
    container_dir = f"{args.container_dir.rstrip('/')}/{release}"
    path = os.path.join(STATE_DIR, "model.staged.env")
    _write_env(
        path,
        {
            "ARABIC_MODEL_DIR": container_dir,
            "EMBEDDINGS_RELEASE": release,
            "MODEL_VERSION": best.version,
        },
    )
    _mark(args.stage_dir, release, "staged", env_file=path)
    return path


def release_name(version: str) -> str:
    return f"mv{version}"


def run(args) -> int:
    dry = args.dry_run
    workdir = tempfile.mkdtemp(prefix="promote_model_")
    try:
        best, best_score, baseline = select_best(args, workdir)
        release = release_name(best.version)
        wins = decide(best_score, baseline, args.margin)
        _log(
            f"best: version {best.version} at {best_score:.4f}, live {baseline:.4f}, "
            f"margin {args.margin}: {'beats the live model' if wins else 'does not beat it'}"
        )
        if not wins:
            raise PromotionError("No version beats the live model by the margin.", EXIT_NO_WINNER)
        steps = _read_steps(args.stage_dir, release)["steps"]
        plan = [s for s in ("exported", "embedded", "staged") if s not in steps]
        if dry:
            _log(
                f"dry run: would stage release {release} ({', '.join(plan) or 'nothing left'}); "
                "nothing was written"
            )
            return 0
        if "exported" not in steps:
            export_step(args, best, release)
        if "embedded" not in steps:
            embed_step(args, best, release)
        env_file = stage_step(args, best, release)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    _log(f"staged release {release}; environment for the new colour: {env_file}")
    _log("to start it and move traffic: tools/promote_model.sh run --promote")
    return 0


# --- prune ----------------------------------------------------------------------------------


def prune(args) -> int:
    """Remove all but the newest `--keep` releases' tables and files; never the live or previous."""
    from database import get_sync_engine, get_sync_session
    from models import EmbeddingSet
    from models.embedding_sets import embedding_table

    protected = {
        v
        for name in ("model.env", "model.env.prev", "model.staged.env")
        for k, v in _read_env(os.path.join(STATE_DIR, name)).items()
        if k == "EMBEDDINGS_RELEASE"
    }
    with get_sync_session() as session:
        rows = session.query(EmbeddingSet).order_by(EmbeddingSet.created_at.desc()).all()
        releases = [r.release for r in rows]
        keep = set(releases[: args.keep]) | protected
        doomed = [r for r in releases if r not in keep]
        _log(f"keeping {sorted(keep & set(releases))}; removing {doomed or 'nothing'}")
        if args.dry_run:
            return 0
        for release in doomed:
            with get_sync_engine().begin() as conn:
                embedding_table(release).drop(conn, checkfirst=True)
            session.query(EmbeddingSet).filter_by(release=release).delete()
            shutil.rmtree(os.path.join(args.stage_dir, release), ignore_errors=True)
        session.commit()
    return 0


# --- command line ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", help="change nothing")
    common.add_argument("--stage-dir", default=os.environ.get("MODEL_STAGE_DIR", DEFAULT_STAGE_DIR))
    run_p = sub.add_parser("run", parents=[common])
    run_p.add_argument("--registered-model", default=os.environ.get("MLFLOW_REGISTERED_MODEL"))
    run_p.add_argument("--versions", type=lambda s: s.split(","), default=None)
    run_p.add_argument("--margin", type=float, default=DEFAULT_MARGIN)
    run_p.add_argument("--pairs-artifact", default=DEFAULT_PAIRS_ARTIFACT)
    run_p.add_argument("--pairs-run-id", default=None)
    run_p.add_argument("--download-dir", default=None, help="cache for downloaded versions")
    run_p.add_argument("--live-model-dir", default=None)
    run_p.add_argument(
        "--container-dir", default=os.environ.get("MODEL_CONTAINER_DIR", DEFAULT_CONTAINER_DIR)
    )
    prune_p = sub.add_parser("prune", parents=[common])
    prune_p.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prune":
            return prune(args)
        if not args.registered_model:
            raise PromotionError(
                "Give --registered-model (or set MLFLOW_REGISTERED_MODEL).", EXIT_USAGE
            )
        if args.live_model_dir is None:
            from scripts.arabic_encoder import model_dir

            args.live_model_dir = model_dir()
        return run(args)
    except PromotionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
