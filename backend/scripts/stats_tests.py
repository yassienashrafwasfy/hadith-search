import json
import os
from datetime import datetime
from itertools import combinations
from typing import Optional

import numpy as np
from scipy import stats as scipy_stats

METRICS = ["AP", "RR", "P@20", "R@20", "F1@20", "nDCG@20"]
DEFAULT_BASELINE = "BM25"
DEFAULT_N_BOOTSTRAP = 10000
DEFAULT_CONFIDENCE = 0.95

METRIC_DISPLAY = {
    "AP": "MAP",
    "RR": "MRR",
    "P@20": "P@20",
    "R@20": "R@20",
    "F1@20": "F1@20",
    "nDCG@20": "nDCG@20",
}


def extract_per_query_metrics(
    results: dict,
    metric_name: str,
    exclude_keys: Optional[set] = None,
) -> dict:
    if exclude_keys is None:
        exclude_keys = {"MEAN"}
    per_system = {}
    for system_name, system_data in results.items():
        values = []
        for qid, qdata in system_data.items():
            if qid in exclude_keys:
                continue
            if "Metrics" in qdata and metric_name in qdata["Metrics"]:
                values.append(float(qdata["Metrics"][metric_name]))
        per_system[system_name] = values
    return per_system


def filter_graded_queries(results: dict, graded_qids: set) -> dict:
    filtered = {}
    for sys_name, sys_data in results.items():
        filtered[sys_name] = {}
        metrics_sum = {}
        count = 0
        for qid, qdata in sys_data.items():
            if qid == "MEAN":
                continue
            if qid in graded_qids:
                filtered[sys_name][qid] = qdata
                if "Metrics" in qdata:
                    for m, v in qdata["Metrics"].items():
                        metrics_sum[m] = metrics_sum.get(m, 0.0) + float(v)
                    count += 1
        if count > 0:
            filtered[sys_name]["MEAN"] = {"Metrics": {m: v / count for m, v in metrics_sum.items()}}
        else:
            filtered[sys_name]["MEAN"] = {"Metrics": {}}
    return filtered


def paired_t_test(system_a: list, system_b: list) -> dict:
    if len(system_a) != len(system_b) or len(system_a) < 2:
        return {
            "test": "paired_t",
            "t_statistic": None,
            "p_value": None,
            "significant_at_0.05": False,
            "significant_at_0.01": False,
            "note": "insufficient data",
        }
    t_stat, p_value = scipy_stats.ttest_rel(system_a, system_b)
    p_valid = not np.isnan(p_value)
    return {
        "test": "paired_t",
        "t_statistic": float(t_stat) if not np.isnan(t_stat) else None,
        "p_value": float(p_value) if p_valid else None,
        "significant_at_0.05": bool(p_value < 0.05) if p_valid else False,
        "significant_at_0.01": bool(p_value < 0.01) if p_valid else False,
    }


def _test_result(test: str, stat_key: str, stat, p_value, note: Optional[str] = None) -> dict:
    """Uniform result dict; `p_value` may be None/NaN (then never significant)."""
    p_valid = p_value is not None and not np.isnan(p_value)
    result = {
        "test": test,
        stat_key: float(stat) if stat is not None and not np.isnan(stat) else None,
        "p_value": float(p_value) if p_valid else None,
        "significant_at_0.05": bool(p_value < 0.05) if p_valid else False,
        "significant_at_0.01": bool(p_value < 0.01) if p_valid else False,
    }
    if note:
        result["note"] = note
    return result


def _wilcoxon_p(system_a: list, system_b: list) -> dict:
    diff = [a - b for a, b in zip(system_a, system_b)]
    if all(d == 0 for d in diff):
        return {"stat": None, "p_value": 1.0, "note": "all differences are zero"}
    try:
        stat, p_value = scipy_stats.wilcoxon(system_a, system_b)
    except ValueError:
        return {"stat": None, "p_value": None, "note": "test failed"}
    return {"stat": stat, "p_value": p_value, "note": None}


def wilcoxon_signed_rank(system_a: list, system_b: list) -> dict:
    if len(system_a) != len(system_b) or len(system_a) < 2:
        return _test_result("wilcoxon", "statistic", None, None, "insufficient data")
    outcome = _wilcoxon_p(system_a, system_b)
    return _test_result(
        "wilcoxon", "statistic", outcome["stat"], outcome["p_value"], outcome["note"]
    )


def bootstrap_ci(
    values: list,
    n_bootstrap: int = DEFAULT_N_BOOTSTRAP,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = 42,
) -> dict:
    if not values or len(values) < 2:
        mean_val = float(np.mean(values)) if values else 0.0
        return {
            "mean": mean_val,
            "ci_lower": mean_val,
            "ci_upper": mean_val,
            "std": 0.0,
        }
    rng = np.random.default_rng(seed)
    arr = np.array(values)
    n = len(arr)
    boot_means = np.array(
        [rng.choice(arr, size=n, replace=True).mean() for _ in range(n_bootstrap)]
    )
    alpha = (1 - confidence) / 2
    return {
        "mean": float(np.mean(arr)),
        "ci_lower": float(np.percentile(boot_means, alpha * 100)),
        "ci_upper": float(np.percentile(boot_means, (1 - alpha) * 100)),
        "std": float(np.std(arr, ddof=1)),
    }


def _direction(mean_diff: float) -> str:
    if mean_diff > 0:
        return "better"
    return "worse" if mean_diff < 0 else "same"


def _paired_comparison(vals_a: list, vals_b: list) -> dict:
    mean_diff = float(np.mean(vals_a) - np.mean(vals_b))
    return {
        "t_test": paired_t_test(vals_a, vals_b),
        "wilcoxon": wilcoxon_signed_rank(vals_a, vals_b),
        "mean_diff": mean_diff,
        "direction": _direction(mean_diff),
        "n_queries": len(vals_a),
    }


def _comparison_pairs(system_names: list, baseline: str) -> list:
    if baseline not in system_names:
        return list(combinations(system_names, 2))
    return [(s, baseline) for s in system_names if s != baseline]


def _comparable(vals_a: list, vals_b: list) -> bool:
    return bool(vals_a) and bool(vals_b) and len(vals_a) == len(vals_b)


def run_pairwise_tests(
    results: dict,
    metrics: Optional[list] = None,
    baseline: str = DEFAULT_BASELINE,
) -> dict:
    metrics = METRICS if metrics is None else metrics
    pairs = _comparison_pairs(list(results.keys()), baseline)

    pairwise = {}
    for metric in metrics:
        per_system = extract_per_query_metrics(results, metric)
        pairwise[metric] = {
            f"{a} vs {b}": _paired_comparison(per_system[a], per_system[b])
            for a, b in pairs
            if _comparable(per_system.get(a, []), per_system.get(b, []))
        }
    return pairwise


def compute_bootstrap_cis(
    results: dict,
    metrics: Optional[list] = None,
    n_bootstrap: int = DEFAULT_N_BOOTSTRAP,
    confidence: float = DEFAULT_CONFIDENCE,
) -> dict:
    if metrics is None:
        metrics = METRICS
    cis = {}
    for system_name, system_data in results.items():
        cis[system_name] = {}
        for metric in metrics:
            per_system = extract_per_query_metrics({system_name: system_data}, metric)
            values = per_system.get(system_name, [])
            cis[system_name][metric] = bootstrap_ci(values, n_bootstrap, confidence)
    return cis


def _mean_metric(results: dict, system_name: str, metric: str) -> float:
    return float(results[system_name].get("MEAN", {}).get("Metrics", {}).get(metric, 0.0))


def _best_by(values: dict) -> Optional[str]:
    return max(values, key=values.get) if values else None


def _significance_marker(test_data: dict) -> str:
    """'**' / '*' when the paired t-test shows a significant improvement, else ''."""
    t_test = test_data.get("t_test", {})
    if t_test.get("p_value") is None or test_data.get("direction", "same") != "better":
        return ""
    if t_test.get("significant_at_0.01"):
        return "**"
    return "*" if t_test.get("significant_at_0.05") else ""


def _bold_if(cell: str, condition: bool) -> str:
    return r"\textbf{" + cell + "}" if condition else cell


def _escape(name: str) -> str:
    return name.replace("_", r"\_")


def _header_row(first_columns: list, metrics: list) -> str:
    parts = [rf"\textbf{{{c}}}" for c in first_columns]
    parts += [rf"\textbf{{{METRIC_DISPLAY.get(m, m)}}}" for m in metrics]
    return " & ".join(parts) + r" \\"


def _latex_table_open(caption: str, label: str, col_spec: str, header: str) -> list:
    return [
        r"\begin{table*}[t]",
        r"\centering",
        caption,
        rf"\label{{{label}}}",
        r"\small",
        rf"\begin{{tabular}}{{{col_spec}}}",
        r"\toprule",
        header,
        r"\midrule",
    ]


_LATEX_TABLE_CLOSE = [r"\bottomrule", r"\end{tabular}", r"\end{table*}"]


def _results_row(sys_name, metrics, mean_metrics, best, pairwise_results, baseline) -> str:
    cells = [_escape(sys_name)]
    for metric in metrics:
        cell = _bold_if(f"{mean_metrics[metric][sys_name]:.3f}", sys_name == best.get(metric))
        if sys_name != baseline:
            test_data = pairwise_results.get(metric, {}).get(f"{sys_name} vs {baseline}", {})
            cell += _significance_marker(test_data)
        cells.append(cell)
    return " & ".join(cells) + r" \\"


def generate_latex_table(
    results: dict,
    pairwise_results: dict,
    metrics: Optional[list] = None,
    baseline: str = DEFAULT_BASELINE,
    k: int = 20,
) -> str:
    metrics = METRICS if metrics is None else metrics
    system_names = list(results.keys())
    mean_metrics = {m: {s: _mean_metric(results, s, m) for s in system_names} for m in metrics}
    best = {m: _best_by(mean_metrics[m]) for m in metrics}

    caption = (
        rf"\caption{{Retrieval performance comparison across {len(system_names)} systems. "
        rf"Best scores are in \textbf{{bold}}. "
        rf"* and ** denote statistically significant improvement "
        rf"over {baseline} at $p<0.05$ and $p<0.01$ (paired t-test).}}"
    )
    lines = _latex_table_open(
        caption, "tab:results", "l" + "c" * len(metrics), _header_row(["Method"], metrics)
    )
    lines += [
        _results_row(s, metrics, mean_metrics, best, pairwise_results, baseline)
        for s in system_names
    ]
    return "\n".join(lines + _LATEX_TABLE_CLOSE)


def run_analysis(
    results: dict,
    metrics: Optional[list] = None,
    baseline: str = DEFAULT_BASELINE,
    n_bootstrap: int = DEFAULT_N_BOOTSTRAP,
    confidence: float = DEFAULT_CONFIDENCE,
    k: int = 20,
) -> dict:
    if metrics is None:
        metrics = METRICS

    system_names = list(results.keys())

    per_system = extract_per_query_metrics(results, metrics[0])
    n_queries = len(per_system.get(system_names[0], [])) if system_names else 0

    pairwise = run_pairwise_tests(results, metrics, baseline)
    cis = compute_bootstrap_cis(results, metrics, n_bootstrap, confidence)

    best_systems = {
        metric: _best_by({s: _mean_metric(results, s, metric) for s in system_names})
        for metric in metrics
        if system_names
    }

    return {
        "metadata": {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "n_queries": n_queries,
            "k": k,
            "baseline": baseline,
            "n_bootstrap": n_bootstrap,
            "confidence_level": confidence,
            "systems": system_names,
        },
        "bootstrap_cis": cis,
        "pairwise_tests": pairwise,
        "summary": {
            "best_systems": best_systems,
        },
    }


E5_DEPENDENT_SYSTEMS = [
    "COSINE_SIMILARITY",
    "BM25_SEMANTIC_RERANK",
    "BM25_RRF",
]

FINETUNE_MODES = ["triplet", "kv_pairs", "combined"]

FINETUNE_MODE_LABELS = {
    "triplet": r"FT(triplet)",
    "kv_pairs": r"FT(kv)",
    "combined": r"FT(combined)",
}


def _system_values(results: dict, system_name: str, metric: str) -> list:
    return extract_per_query_metrics({system_name: results[system_name]}, metric).get(
        system_name, []
    )


def _cross_config_comparison(ft_vals: list, baseline_vals: list) -> dict:
    return {
        **_paired_comparison(ft_vals, baseline_vals),
        "baseline_mean": float(np.mean(baseline_vals)),
        "finetuned_mean": float(np.mean(ft_vals)),
    }


def _cross_config_metric(system_name, metric, baseline_vals, finetuned_results) -> dict:
    comparisons = {}
    for mode, ft_results in finetuned_results.items():
        if system_name not in ft_results:
            continue
        ft_vals = _system_values(ft_results, system_name, metric)
        if _comparable(ft_vals, baseline_vals):
            comparisons[f"{mode} vs baseline"] = _cross_config_comparison(ft_vals, baseline_vals)
    return comparisons


def run_cross_config_tests(
    baseline_results: dict,
    finetuned_results: dict,
    metrics: Optional[list] = None,
    e5_systems: Optional[list] = None,
) -> dict:
    metrics = METRICS if metrics is None else metrics
    e5_systems = E5_DEPENDENT_SYSTEMS if e5_systems is None else e5_systems

    tests = {}
    for system_name in e5_systems:
        if system_name not in baseline_results:
            continue
        tests[system_name] = {}
        for metric in metrics:
            baseline_vals = _system_values(baseline_results, system_name, metric)
            if baseline_vals:
                tests[system_name][metric] = _cross_config_metric(
                    system_name, metric, baseline_vals, finetuned_results
                )
    return tests


def _mode_means(baseline_results, finetuned_results, system_name, metrics) -> dict:
    """{metric: {"baseline": v, mode: v, ...}} of MEAN metrics for one system."""
    all_vals = {}
    for metric in metrics:
        vals = {"baseline": _mean_metric(baseline_results, system_name, metric)}
        for mode, ft_results in finetuned_results.items():
            if system_name in ft_results:
                vals[mode] = _mean_metric(ft_results, system_name, metric)
        all_vals[metric] = vals
    return all_vals


def _comparison_cell(val, is_best, test_data=None) -> str:
    return _bold_if(f"{val:.3f}", is_best) + _significance_marker(test_data or {})


def _comparison_rows(system_name, metrics, all_vals, finetuned_results, cross_config) -> list:
    best = {m: _best_by(all_vals[m]) for m in metrics}
    rows = [
        " & ".join(
            [_escape(system_name), "Baseline"]
            + [
                _comparison_cell(all_vals[m].get("baseline", 0.0), best[m] == "baseline")
                for m in metrics
            ]
        )
        + r" \\"
    ]
    for mode in FINETUNE_MODES:
        if system_name not in finetuned_results.get(mode, {}):
            continue
        key = f"{mode} vs baseline"
        cells = [
            _comparison_cell(
                all_vals[m].get(mode, 0.0),
                best[m] == mode,
                cross_config.get(system_name, {}).get(m, {}).get(key, {}),
            )
            for m in metrics
        ]
        rows.append(" & ".join(["", FINETUNE_MODE_LABELS[mode]] + cells) + r" \\")
    return rows


_COMPARISON_CAPTION = (
    r"\caption{Impact of LoRA fine-tuning on E5-dependent retrieval systems. "
    r"Baseline uses pretrained \mbox{multilingual-e5-large}. "
    r"FT(triplet), FT(kv), and FT(combined) use LoRA adapters trained with "
    r"contrastive, cross-concept alignment, and combined objectives. "
    r"Best per system per metric in \textbf{bold}. "
    r"* and ** denote significant improvement over baseline "
    r"at $p<0.05$ and $p<0.01$ (paired t-test).}"
)


def generate_comparison_latex_table(
    baseline_results: dict,
    finetuned_results: dict,
    cross_config: dict,
    metrics: Optional[list] = None,
    e5_systems: Optional[list] = None,
) -> str:
    metrics = METRICS if metrics is None else metrics
    e5_systems = E5_DEPENDENT_SYSTEMS if e5_systems is None else e5_systems

    lines = _latex_table_open(
        _COMPARISON_CAPTION,
        "tab:finetune-comparison",
        "ll" + "c" * len(metrics),
        _header_row(["System", "Mode"], metrics),
    )
    for system_name in e5_systems:
        if system_name not in baseline_results:
            continue
        all_vals = _mode_means(baseline_results, finetuned_results, system_name, metrics)
        lines += _comparison_rows(system_name, metrics, all_vals, finetuned_results, cross_config)
        lines.append(r"\midrule")

    if lines[-1] == r"\midrule":
        lines.pop()
    return "\n".join(lines + _LATEX_TABLE_CLOSE)


def _delta_entry(baseline_val: float, ft_val: float) -> dict:
    delta_pct = ((ft_val - baseline_val) / baseline_val) * 100 if baseline_val > 0 else 0.0
    return {
        "baseline": float(baseline_val),
        "finetuned": float(ft_val),
        "delta": float(ft_val - baseline_val),
        "delta_pct": round(delta_pct, 2),
    }


def _system_deltas(baseline_results, finetuned_results, system_name, metrics) -> dict:
    return {
        metric: {
            mode: _delta_entry(
                _mean_metric(baseline_results, system_name, metric),
                _mean_metric(ft_results, system_name, metric),
            )
            for mode, ft_results in finetuned_results.items()
            if system_name in ft_results
        }
        for metric in metrics
    }


def _average_deltas(deltas: dict, finetuned_results: dict, metrics: list) -> dict:
    averages = {}
    for metric in metrics:
        averages[metric] = {}
        for mode in FINETUNE_MODES:
            pct_vals = [
                per_metric[metric][mode]["delta_pct"]
                for per_metric in deltas.values()
                if mode in per_metric.get(metric, {})
            ]
            if mode in finetuned_results and pct_vals:
                averages[metric][mode] = round(sum(pct_vals) / len(pct_vals), 2)
    return averages


def generate_delta_table(
    baseline_results: dict,
    finetuned_results: dict,
    metrics: Optional[list] = None,
    e5_systems: Optional[list] = None,
) -> dict:
    metrics = METRICS if metrics is None else metrics
    e5_systems = E5_DEPENDENT_SYSTEMS if e5_systems is None else e5_systems

    deltas = {
        system_name: _system_deltas(baseline_results, finetuned_results, system_name, metrics)
        for system_name in e5_systems
        if system_name in baseline_results
    }
    return {
        "per_system": deltas,
        "averages": _average_deltas(deltas, finetuned_results, metrics),
    }


def _format_delta(avg: Optional[float]) -> str:
    return "--" if avg is None else f"{'+' if avg >= 0 else ''}{avg:.1f}\\%"


def _mean_or_none(values: list) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _delta_row(system_name, available_modes, per_system, metrics) -> str:
    cells = [_escape(system_name)]
    for mode in available_modes:
        pct = [
            per_system[system_name][m][mode]["delta_pct"]
            for m in metrics
            if per_system.get(system_name, {}).get(m, {}).get(mode)
        ]
        cells.append(_format_delta(_mean_or_none(pct)))
    return " & ".join(cells) + r" \\"


def _delta_average_row(available_modes, averages, metrics) -> str:
    cells = [r"\textbf{Average}"]
    for mode in available_modes:
        vals = [averages[m][mode] for m in metrics if averages.get(m, {}).get(mode) is not None]
        avg = _mean_or_none(vals)
        cells.append("--" if avg is None else rf"\textbf{{{_format_delta(avg)}}}")
    return " & ".join(cells) + r" \\"


_DELTA_CAPTION = (
    r"\caption{Relative improvement (\%) from LoRA fine-tuning "
    r"averaged across 6 IR metrics. Positive values indicate "
    r"improvement over baseline.}"
)


def generate_delta_latex_table(delta_data: dict, metrics: Optional[list] = None) -> str:
    metrics = METRICS if metrics is None else metrics
    averages = delta_data.get("averages", {})
    available_modes = [m for m in FINETUNE_MODES if m in averages.get(metrics[0], {})]
    if not available_modes:
        return ""

    per_system = delta_data.get("per_system", {})
    header = " & ".join(
        [r"\textbf{System}"] + [rf"\textbf{{{FINETUNE_MODE_LABELS[m]}}}" for m in available_modes]
    )
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        _DELTA_CAPTION,
        r"\label{tab:finetune-delta}",
        r"\small",
        rf"\begin{{tabular}}{{{'l' + 'c' * len(available_modes)}}}",
        r"\toprule",
        header + r" \\",
        r"\midrule",
    ]
    lines += [_delta_row(s, available_modes, per_system, metrics) for s in per_system]
    lines += [r"\midrule", _delta_average_row(available_modes, averages, metrics)]
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines)


if __name__ == "__main__":
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(BASE_DIR, "..", "data")
    RESULTS_PATH = os.path.join(DATA_DIR, "qrels_results.json")
    STATS_PATH = os.path.join(DATA_DIR, "stats_results.json")
    LATEX_PATH = os.path.join(DATA_DIR, "results_table.tex")

    with open(RESULTS_PATH, encoding="utf-8") as f:
        results = json.load(f)

    print(f"Loaded results from {RESULTS_PATH}")
    print(f"Systems: {list(results.keys())}")

    stats_output = run_analysis(results, baseline="BM25")
    latex_table = generate_latex_table(
        results,
        stats_output["pairwise_tests"],
        baseline="BM25",
    )

    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats_output, f, indent=2, ensure_ascii=False)
    with open(LATEX_PATH, "w", encoding="utf-8") as f:
        f.write(latex_table)

    print(f"\nStats saved -> {STATS_PATH}")
    print(f"LaTeX table -> {LATEX_PATH}")
    print("\nBest systems per metric:")
    for metric, best_sys in stats_output["summary"]["best_systems"].items():
        mean_val = results[best_sys].get("MEAN", {}).get("Metrics", {}).get(metric, 0.0)
        print(f"  {metric}: {best_sys} ({float(mean_val):.4f})")
