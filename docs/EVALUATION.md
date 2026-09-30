# Evaluation Pipeline

> **Note:** dense systems (cosine, semantic rerank, RRF) now run on Arabic queries only (item 23). The fine-tuning sections mention the old E5 model and are switched off.

> This document describes the full evaluation methodology: queries, relevance judgments, metrics, statistical testing, and the baseline vs. fine-tuned comparison framework.

---

## Overview

Evaluation follows the standard TREC-style IR evaluation methodology:

1. **Queries**: 20 bilingual evaluation queries
2. **Pooling**: Candidate hadiths retrieved by all systems, unioned per query
3. **Relevance judgments**: Human annotators grade pooled candidates
4. **Metrics**: Standard IR metrics at k=20
5. **Statistical significance**: Paired t-test, Wilcoxon, bootstrap CI

---

## Evaluation Queries

**File**: `backend/data/queries.json`  
**Count**: 20 queries — 10 English, 10 Arabic

| ID | Query |
|----|-------|
| EN1 | a man asked the prophet about the best deed |
| EN2 | be kind to those who are close to you |
| EN3 | the tongue is the cause of most sins |
| EN4 | rules of giving charity |
| EN5 | women's rights and marital disputes |
| EN6 | virtues of honesty and truthfulness |
| EN7 | the etiquette and rites of pilgrimage |
| EN8 | what invalidates ablution |
| EN9 | prohibition of backbiting |
| EN10 | rights of parents |
| AR1 | علامات الساعة (signs of the hour) |
| AR2 | عقوبة الكذب (punishment for lying) |
| AR3 | التعامل مع غير المسلمين (dealing with non-Muslims) |
| AR4 | فضل الصيام (virtue of fasting) |
| AR5 | ما يبطل الصلاة (what invalidates prayer) |
| AR6 | فضل ذكر الله (virtue of remembrance of God) |
| AR7 | علامات المنافق (signs of the hypocrite) |
| AR8 | كيف نكفر عن سيئاتنا (how to atone for sins) |
| AR9 | كيف نقوي إيماننا (how to strengthen faith) |
| AR10 | ماذا يفعل الإنسان عند الغضب (what to do when angry) |

These queries cover diverse topics across the hadith corpus and are balanced between Arabic and English to evaluate cross-lingual retrieval performance.

---

## Training Queries

**File**: `backend/data/training_queries.json`  
**Count**: 100 queries — 50 English (`ENT01`–`ENT50`), 50 Arabic (`ART01`–`ART50`)

These queries are **not used for evaluation**. They are used exclusively for:
- LLM-graded qrels generation (`training_qrels_graded.json`)
- Fine-tuning training data (triplet mode and combined mode)

The training queries must remain strictly separate from the evaluation queries to prevent data leakage.

---

## Pooling

**File**: `backend/scripts/pooling.py`  
**Output**: `backend/data/qrels_ungraded.json` and `backend/data/pooling_manifest.json`

Pooling is the standard IR methodology for constructing relevance judgment sets without exhaustively annotating the entire corpus.

**Procedure**:
1. Run the five selected contributors (BM25, BM25_ROCCHIO, COSINE_SIMILARITY, BM25_SEMANTIC_RERANK, BM25_RRF) over each of the 20 eval queries
2. For each query, take the union of top-k results across all systems
3. Write the pooled candidate set to `qrels_ungraded.json`
4. Write per-query pool diagnostics to `pooling_manifest.json`

**Depth**: The current pool depth is top-50 per system, configured in `pooling.py`. A deeper pool gives more complete relevance judgments but increases annotation burden.

**Manifest**: `pooling_manifest.json` records pool depth, the list of systems used, per-query pool sizes, per-system contributions, unique per-system contributions, and any system errors. `qrels_ungraded.json` intentionally keeps the older `{query_id: [hadith_ids...]}` shape for annotation-platform compatibility.

Pooling fails loudly if any evaluated system fails, because a partial pool would weaken the paper methodology. For debugging only, set `ALLOW_PARTIAL_POOLING=1` to write a partial `qrels_ungraded.json`; the manifest will still record the failed systems.

**Note**: Pooling must be rerun if the corpus changes (new `hadiths.db`) or if new retrieval systems are added. After any rerun, annotation must restart because the candidate hadith IDs change.

---

## Human Relevance Judgments

**File**: `backend/data/qrels_graded.json` (requires export/adjudication of platform annotations)

Each pooled (query, hadith) pair is graded by 3 annotators as:
- `2` — highly/directly relevant
- `1` — relevant/partial: this hadith is a useful result for the query
- `0` — not relevant

**Annotation platform**: See `docs/WIKI.md`, Section 10. Deployed with `APP_MODE=annotation`.

**Inter-annotator agreement**: Cohen's Kappa is computed pairwise across annotators. The annotation router exposes this via API. A Kappa ≥ 0.40 (moderate agreement) is the minimum acceptable for a publishable benchmark.

**Adjudication**: When annotators disagree, the majority vote (2 out of 3) determines the final grade.

**Current status**: Human qrel export is pending. Binary metrics count only grades greater than zero; nDCG uses graded gains. The default evaluation cutoff is 20, and P@k divides by k even for short result lists.

---

## Metrics

All metrics are computed at k=20. Implementations are in `backend/scripts/evaluation.py`.

| Metric | Description |
|--------|-------------|
| **P@k** | Precision at k: fraction of top-k results that are relevant |
| **R@k** | Recall at k: fraction of all relevant documents found in top-k |
| **F1@k** | Harmonic mean of P@k and R@k |
| **AP** | Average Precision: area under the precision-recall curve |
| **MAP** | Mean Average Precision: mean of AP across queries |
| **nDCG@k** | Normalized Discounted Cumulative Gain: position-weighted relevance metric |
| **MRR** | Mean Reciprocal Rank: mean of 1/rank of first relevant result |

**Primary metric for the paper**: nDCG@20 — it is the most informative single metric for ranked retrieval evaluation because it accounts for both relevance and ranking position.

**Gain function for nDCG**: Binary relevance (0 or 1), exponential gain formulation: `gain = 2^rel - 1`.

---

## Evaluation Scripts

### Baseline Evaluation

```powershell
python scripts\evaluation.py
```

Evaluates all 11 retrieval systems against human-graded qrels.

**Output files**:
- `qrels_results.json` — per-query, per-system scores
- `stats_results.json` — significance test results
- `results_table.tex` — LaTeX table (11 systems × 6 metrics)

### Fine-Tuned Evaluation

```powershell
python scripts\finetune_eval.py --mode triplet
python scripts\finetune_eval.py --mode kv_pairs
python scripts\finetune_eval.py --mode combined
```

Each run re-encodes the corpus with the specified LoRA adapter and evaluates the E5-dependent systems.

**Output**: `backend/data/finetuned_results_{mode}.json`

### Full Comparison

```powershell
python scripts\full_evaluation.py
```

Compares baseline vs all 3 fine-tuned modes across all E5-dependent systems.

**Output files**:
- `comparison_table.tex` — 4 conditions × 5 systems × 6 metrics
- `delta_table.tex` — relative improvement (%) of each fine-tuned mode vs baseline
- `comparison_results.json` — full data
- Timestamped archives of all outputs

---

## Statistical Significance Testing

**File**: `backend/scripts/stats_tests.py`

Three tests are run for each pair of systems being compared:

| Test | What it measures |
|------|-----------------|
| **Paired t-test** | Mean difference in per-query metric scores, assumes normality |
| **Wilcoxon signed-rank** | Non-parametric paired test, more robust for small n |
| **Bootstrap CI** | 95% confidence interval on mean difference (1000 iterations) |

A comparison is considered statistically significant at p < 0.05.

With only 20 queries, statistical power is limited. The paper should report all three test results and acknowledge this limitation.

**Benchmark endpoint**: `/api/v1/benchmark/stats` returns significance test results as JSON for the web UI.

---

## LLM Grader Validation

**File**: `backend/scripts/llm_validation.py`

Before using LLM grades as training signal, the LLM grader is validated against human judgments:

1. Run `llm_grader.py --validate` to grade the 20 eval queries with the LLM
2. Run `llm_validation.py` to compute agreement between LLM and human grades

**Agreement metrics**:
- Cohen's Kappa — inter-rater reliability
- Spearman's ρ — rank correlation of relevance scores

**Threshold for paper**: Kappa ≥ 0.40 (moderate agreement) must be reported before LLM grades are used as training data. This is a methodological safeguard against training on low-quality automatic labels.

**Output**: `llm_validation_report.json`, `llm_validation_table.tex`

---

## Expected Paper Tables

### Table 1: Baseline Retrieval Results (all 11 systems)

| System | P@20 | R@20 | F1@20 | MAP | nDCG@20 | MRR |
|--------|------|------|-------|-----|---------|-----|
| Term Overlap | | | | | | |
| TF-IDF | | | | | | |
| BM25 | | | | | | |
| BM25 + TF-IDF | | | | | | |
| BM25 + PRF | | | | | | |
| BM25 + TF-IDF + PRF | | | | | | |
| Cosine (E5 baseline) | | | | | | |
| Semantic Rerank | | | | | | |
| Semantic RRF | | | | | | |

### Table 2: Fine-Tuning Comparison (E5-dependent systems only)

| System | Baseline | triplet | kv_pairs | combined |
|--------|----------|---------|----------|----------|
| Cosine Similarity | nDCG | nDCG | nDCG | nDCG |
| Semantic Rerank | | | | |
| Semantic RRF | | | | |

(Full 6-metric version generated by `full_evaluation.py`)

### Table 3: Relative Improvement (Δ% vs baseline)

Same structure as Table 2 but showing percentage improvement per mode.

---

## Reproducibility

- All metric computations are deterministic given the same `qrels_graded.json` and retrieval results.
- Bootstrap CI uses a fixed seed (configurable in `stats_tests.py`).
- All LaTeX tables are generated programmatically — no manual table editing.
- Timestamped archives of all evaluation outputs are written by `full_evaluation.py` to prevent accidental overwrite.
