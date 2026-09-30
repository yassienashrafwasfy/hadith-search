"""
Full evaluation orchestration: baseline + 3 fine-tuned modes.

Loads existing evaluation results and generates:
- Cross-config significance tests (baseline vs each fine-tuned mode)
- Comparison LaTeX table (E5-dependent systems x all modes)
- Delta table (relative improvement %)
- Unified comparison JSON

Prerequisites:
    1. Baseline:   python -m scripts.evaluation
    2. Fine-tune:  python -m scripts.finetune --mode {triplet,kv_pairs,combined}
    3. FT eval:    python -m scripts.finetune_eval --mode {triplet,kv_pairs,combined}

Usage:
    python -m scripts.full_evaluation
    python -m scripts.full_evaluation --baseline BM25 --k 20
"""

import argparse
import json
import os
from datetime import datetime

from scripts.stats_tests import (
    E5_DEPENDENT_SYSTEMS,
    FINETUNE_MODES,
    METRICS,
    filter_graded_queries,
    generate_comparison_latex_table,
    generate_delta_latex_table,
    generate_delta_table,
    generate_latex_table,
    run_analysis,
    run_cross_config_tests,
)

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPTS_DIR, "..", "data")


def load_results(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Full evaluation: baseline + fine-tuned comparison"
    )
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument(
        "--baseline",
        default="BM25",
        help="Baseline system for significance tests",
    )
    return parser.parse_args(argv)


def _load_baseline(data_dir):
    path = os.path.join(data_dir, "qrels_results.json")
    results = load_results(path)
    if results is None:
        print(f"ERROR: Baseline results not found at {path}")
        print("Run: python -m scripts.evaluation")
        return None
    print(f"\nBaseline loaded: {len(results)} systems")
    print(f"  Systems: {list(results.keys())}")
    return results


def _load_finetuned(data_dir):
    loaded = {}
    for mode in FINETUNE_MODES:
        path = os.path.join(data_dir, f"finetuned_results_{mode}.json")
        results = load_results(path)
        if results is None:
            print(f"WARNING: Fine-tuned results not found for '{mode}'")
            print(f"  Expected: {path}")
            print(f"  Run: python -m scripts.finetune_eval --mode {mode}")
            continue
        loaded[mode] = results
        print(f"Fine-tuned loaded: {mode} ({len(results)} systems)")
    return loaded


def _load_graded_qids(data_dir):
    path = os.path.join(data_dir, "qrels_graded.json")
    qrels = load_results(path)
    if qrels is None:
        print(f"ERROR: Graded qrels not found at {path}")
        return None
    qids = {qid for qid, data in qrels.items() if data.get("grades")}
    print(f"\nGraded queries: {len(qids)}")
    return qids


def _run_baseline_analysis(baseline, args, data_dir):
    print("\n--- Baseline Analysis ---")
    stats = run_analysis(baseline, baseline=args.baseline, k=args.k)
    latex = generate_latex_table(
        baseline, stats["pairwise_tests"], baseline=args.baseline, k=args.k
    )
    stats_path = os.path.join(data_dir, "stats_results.json")
    latex_path = os.path.join(data_dir, "results_table.tex")
    _write_json(stats_path, stats)
    _write_text(latex_path, latex)
    print(f"  Stats  -> {stats_path}")
    print(f"  LaTeX  -> {latex_path}")
    return stats


def _count_comparisons(cross_config):
    return sum(
        len(metric_data) for sys_data in cross_config.values() for metric_data in sys_data.values()
    )


def _run_cross_config(baseline, finetuned):
    print("\n--- Cross-Config Significance Tests ---")
    cross_config = run_cross_config_tests(baseline, finetuned, e5_systems=E5_DEPENDENT_SYSTEMS)
    print(f"  {_count_comparisons(cross_config)} comparisons across {len(cross_config)} systems")
    return cross_config


def _write_comparison_tables(baseline, finetuned, cross_config, data_dir):
    """Comparison + delta LaTeX tables; returns (delta_data, {path: content})."""
    print("\n--- Comparison LaTeX Table ---")
    comparison_latex = generate_comparison_latex_table(
        baseline, finetuned, cross_config, e5_systems=E5_DEPENDENT_SYSTEMS
    )
    comparison_path = os.path.join(data_dir, "comparison_table.tex")
    _write_text(comparison_path, comparison_latex)
    print(f"  -> {comparison_path}")

    print("\n--- Delta Table ---")
    delta_data = generate_delta_table(baseline, finetuned, e5_systems=E5_DEPENDENT_SYSTEMS)
    delta_latex = generate_delta_latex_table(delta_data)
    delta_path = os.path.join(data_dir, "delta_table.tex")
    _write_text(delta_path, delta_latex)
    print(f"  -> {delta_path}")
    return delta_data, {comparison_path: comparison_latex, delta_path: delta_latex}


def _comparison_json(graded_qids, finetuned, args, baseline_stats, cross_config, delta_data):
    return {
        "metadata": {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "n_graded_queries": len(graded_qids),
            "graded_qids": sorted(graded_qids),
            "baseline_system": args.baseline,
            "k": args.k,
            "modes": list(finetuned.keys()),
            "e5_systems": E5_DEPENDENT_SYSTEMS,
            "metrics": METRICS,
        },
        "baseline_stats": baseline_stats,
        "cross_config_tests": cross_config,
        "delta": delta_data,
    }


def _archive(artifacts, timestamp):
    """Write a timestamped copy of each {path: text-or-json-data} artifact."""
    for path, content in artifacts.items():
        stamped = path.replace(".", f"_{timestamp}.")
        if path.endswith(".json"):
            _write_json(stamped, content)
        else:
            _write_text(stamped, content)
        print(f"  Archived        -> {stamped}")


def _mode_deltas(delta_data, system_name):
    """Average delta % over all metrics, per fine-tuning mode, for one system."""
    averages = delta_data["averages"].get(METRICS[0], {})
    per_metric = delta_data["per_system"].get(system_name, {})
    deltas = {}
    for mode in FINETUNE_MODES:
        pct = [per_metric[m][mode]["delta_pct"] for m in METRICS if per_metric.get(m, {}).get(mode)]
        if mode in averages and pct:
            deltas[mode] = sum(pct) / len(pct)
    return deltas


def _format_deltas(mode_deltas):
    ranked = sorted(mode_deltas.items(), key=lambda x: x[1], reverse=True)
    return "  ".join(f"{m}: {'+' if v >= 0 else ''}{v:.1f}%" for m, v in ranked)


def _print_summary(baseline, finetuned, delta_data):
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"Baseline systems:    {len(baseline)}")
    print(f"Fine-tuned modes:    {list(finetuned.keys())}")
    available_e5 = [s for s in E5_DEPENDENT_SYSTEMS if s in baseline]
    print(f"E5 systems analyzed: {len(available_e5)}")
    print(f"  {available_e5}")

    print("\nBest fine-tuning mode per system (avg delta %):")
    for system_name in E5_DEPENDENT_SYSTEMS:
        if system_name not in delta_data["per_system"]:
            continue
        mode_deltas = _mode_deltas(delta_data, system_name)
        if mode_deltas:
            print(f"  {system_name:30s}  {_format_deltas(mode_deltas)}")


def _load_inputs(data_dir):
    """(baseline, finetuned, graded_qids) or None when a required input is missing."""
    baseline = _load_baseline(data_dir)
    if baseline is None:
        return None
    finetuned = _load_finetuned(data_dir)
    graded_qids = _load_graded_qids(data_dir)
    if graded_qids is None:
        return None
    return baseline, finetuned, graded_qids


def _compare_finetuned(baseline, finetuned, graded_qids, baseline_stats, args, data_dir):
    cross_config = _run_cross_config(baseline, finetuned)
    delta_data, tables = _write_comparison_tables(baseline, finetuned, cross_config, data_dir)
    comparison = _comparison_json(
        graded_qids, finetuned, args, baseline_stats, cross_config, delta_data
    )
    json_path = os.path.join(data_dir, "comparison_results.json")
    _write_json(json_path, comparison)
    print(f"\n  Comparison JSON -> {json_path}")
    _archive({**tables, json_path: comparison}, datetime.now().strftime("%Y%m%d_%H%M%S"))
    _print_summary(baseline, finetuned, delta_data)


def main(argv=None):
    args = _parse_args(argv)
    print("=" * 60)
    print("FULL EVALUATION: Baseline + Fine-tuned Comparison")
    print("=" * 60)

    inputs = _load_inputs(DATA_DIR)
    if inputs is None:
        return
    baseline_raw, finetuned_raw, graded_qids = inputs

    baseline = filter_graded_queries(baseline_raw, graded_qids)
    finetuned = {m: filter_graded_queries(r, graded_qids) for m, r in finetuned_raw.items()}
    baseline_stats = _run_baseline_analysis(baseline, args, DATA_DIR)
    if not finetuned:
        print("\nNo fine-tuned results found. Done.")
        return
    _compare_finetuned(baseline, finetuned, graded_qids, baseline_stats, args, DATA_DIR)


if __name__ == "__main__":
    main()
