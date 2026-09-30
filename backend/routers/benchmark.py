import json
import os

from fastapi import APIRouter, HTTPException, Query, Request

from rest import API_PREFIX, href, json_response, link

router = APIRouter(prefix=f"{API_PREFIX}/benchmark", tags=["benchmark"])

CACHE_SECONDS = 300  # results only change when an evaluation script is re-run
_MODE = r"^[A-Za-z0-9_]+$"  # mode is part of a file name, so no path separators

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(BASE_DIR), "data")
QUERIES_PATH = os.path.join(DATA_DIR, "queries.json")
QRELS_RESULTS_PATH = os.path.join(DATA_DIR, "qrels_results.json")
STATS_RESULTS_PATH = os.path.join(DATA_DIR, "stats_results.json")
FINETUNED_RESULTS_TEMPLATE = os.path.join(DATA_DIR, "finetuned_results_{mode}.json")
FINETUNED_STATS_TEMPLATE = os.path.join(DATA_DIR, "finetuned_stats_{mode}.json")
COMPARISON_RESULTS_PATH = os.path.join(DATA_DIR, "comparison_results.json")


def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _stored_results(request: Request, path: str, missing: str):
    """The JSON file as a cacheable response, or 404 when the evaluation has not been run."""
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=missing)
    return json_response(request, load_json(path), max_age=CACHE_SECONDS)


@router.get("")
def benchmark_index():
    names = ("results", "stats", "qrels", "finetuned", "finetuned-stats", "comparison")
    return {"_links": {name: link(href("benchmark", name)) for name in names}}


@router.get("/results")
def benchmark_results(request: Request):
    return _stored_results(request, QRELS_RESULTS_PATH, "No benchmark results found.")


@router.get("/stats")
def benchmark_stats(request: Request):
    return _stored_results(
        request, STATS_RESULTS_PATH, "No stats results found. Run evaluation first."
    )


@router.get("/qrels")
def benchmark_qrels(request: Request):
    queries_data = load_json(QUERIES_PATH)

    enhanced = {}
    for qid, query_text in queries_data.items():
        enhanced[qid] = {"query": query_text, "grades": {}}

    body = {
        "description": (
            "Generated from a stratified sample of 2000 hadiths across books and chapters. "
            "Results reflect only those 2000 hadiths for fair comparison, "
            "covering all currently available algorithms."
        ),
        "qrels": enhanced,
    }
    return json_response(request, body, max_age=CACHE_SECONDS)


@router.get("/finetuned")
def finetuned_results(request: Request, mode: str = Query("combined", pattern=_MODE)):
    return _stored_results(
        request,
        FINETUNED_RESULTS_TEMPLATE.format(mode=mode),
        f"No fine-tuned results found for mode '{mode}'. Run finetune_eval.py first.",
    )


@router.get("/finetuned-stats")
def finetuned_stats(request: Request, mode: str = Query("combined", pattern=_MODE)):
    return _stored_results(
        request,
        FINETUNED_STATS_TEMPLATE.format(mode=mode),
        f"No fine-tuned stats found for mode '{mode}'. Run finetune_eval.py first.",
    )


@router.get("/comparison")
def comparison_results(request: Request):
    return _stored_results(
        request,
        COMPARISON_RESULTS_PATH,
        "No comparison results found. Run full_evaluation.py first.",
    )
