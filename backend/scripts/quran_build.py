"""Build passages, surahs, embeddings and summaries for a loaded Quran source.

    python scripts/quran_build.py --source quran
    python scripts/quran_build.py --source ibn_kathir --no-summaries

Needs the ayat loaded by `quran_load.py`, the Fada ONNX model, and (for summaries) a running
llama-server at QURAN_ANSWERER_URL. Re-running replaces earlier passage and surah nodes.
"""

import argparse
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from quran import ingest  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", required=True, help="source (schema) name")
    p.add_argument("--no-summaries", action="store_true", help="skip the LLM summaries")
    p.add_argument("--percentile", type=float, default=25.0, help="chunking split percentile")
    p.add_argument("--min-tokens", type=int, default=40)
    p.add_argument("--max-tokens", type=int, default=200)
    p.add_argument("--concurrency", type=int, default=1, help="parallel summary requests")
    return p


def token_counter(encoder):
    """Token count with the encoder's own tokenizer (falls back to words for other encoders)."""
    tokenizer = getattr(encoder, "_tokenizer", None)
    if tokenizer is None:
        return ingest.word_count
    return lambda text: len(tokenizer.encode(text).ids)


def run(args: argparse.Namespace) -> ingest.BuildStats:
    from database import get_sync_session
    from quran.llm import LlamaServerClient
    from quran.models import node_table
    from scripts.arabic_encoder import load_encoder
    from settings import get_settings

    settings = get_settings()
    encoder = load_encoder(threads=0)
    llm = None
    if not args.no_summaries:
        llm = LlamaServerClient(settings.quran_answerer_url, settings.quran_llm_timeout_seconds)
    with get_sync_session() as session:
        stats = ingest.build_source(
            session,
            node_table(args.source),
            encoder,
            llm,
            count=token_counter(encoder),
            concurrency=args.concurrency,
            percentile=args.percentile,
            min_tokens=args.min_tokens,
            max_tokens=args.max_tokens,
        )
        session.commit()
    print(
        f"{args.source}: {stats.ayat} ayat, {stats.passages} passages, {stats.surahs} surahs, "
        f"{stats.summarised} summaries"
    )
    return stats


def main(argv: list[str] | None = None) -> ingest.BuildStats:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    main()
