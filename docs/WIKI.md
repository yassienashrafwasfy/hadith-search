# Project Wiki — Bilingual Hadith Retrieval System

> Comprehensive reference for any developer or agent working on this project. Read this first before touching any code.

---

## Table of Contents

1. [What This Project Is](#1-what-this-project-is)
2. [Research Goal and Paper Claim](#2-research-goal-and-paper-claim)
3. [Repository Layout](#3-repository-layout)
4. [Data Pipeline — Overview](#4-data-pipeline--overview)
5. [Corpus](#5-corpus)
6. [Preprocessing](#6-preprocessing)
7. [Retrieval Systems](#7-retrieval-systems)
8. [Fine-Tuning](#8-fine-tuning)
9. [Evaluation](#9-evaluation)
10. [Annotation Platform](#10-annotation-platform)
11. [LLM Grading Pipeline](#11-llm-grading-pipeline)
12. [KV Pair Generation](#12-kv-pair-generation)
13. [Backend API](#13-backend-api)
14. [Frontend](#14-frontend)
15. [Deployment](#15-deployment)
16. [Open Questions and Known Issues](#16-open-questions-and-known-issues)
17. [Key Invariants — Never Break These](#17-key-invariants--never-break-these)

---

## 1. What This Project Is

A full-stack bilingual (English + Arabic) hadith information retrieval research system. It serves two purposes simultaneously:

1. **A research platform**: implements and benchmarks 11 retrieval systems, supports human annotation of relevance judgments, runs LLM-based automatic grading, and fine-tunes dense embedding models with LoRA.
2. **A live search tool**: a FastAPI + React web application where users can search hadith in English or Arabic.

The system is built for an academic paper targeting ECIR 2026.

---

## 2. Research Goal and Paper Claim

**Central contribution**: Empirically compare three LoRA fine-tuning strategies for the `intfloat/multilingual-e5-large` dense embedding model on hadith retrieval data, and determine which strategy produces the best retrieval performance.

**Secondary contribution**: The evaluation benchmark itself — 20 bilingual queries with human relevance judgments over the LK Hadith Corpus — is released as a community resource.

**The three fine-tuning strategies** (see `docs/FINE_TUNING.md` for full details):

| Mode | Training signal | Hypothesis |
|------|----------------|------------|
| `triplet` | LLM-graded qrels (query → relevant hadiths) | Standard dense retrieval fine-tuning |
| `kv_pairs` | Verified concept-entity pairs | Domain vocabulary alignment via KV pairs teaches Islamic terminology E5 didn't learn during pretraining |
| `combined` | Both datasets merged | Combining both signals achieves the best of both |

**What the paper does NOT claim**: That any single fine-tuning mode is definitively best — the experiments determine this empirically. The paper reports whichever mode wins and discusses why.

**Baseline**: `intfloat/multilingual-e5-large` pretrained, no fine-tuning.

---

## 3. Repository Layout

```
hadith-search/
├── docs/                         # This documentation folder
│   ├── WIKI.md                   # You are here
│   ├── CORPUS_DECISIONS.md       # All data filtering decisions (paper-ready)
│   ├── FINE_TUNING.md            # Fine-tuning methodology and rationale
│   ├── ARCHITECTURE.md           # System architecture reference
│   └── EVALUATION.md             # Evaluation pipeline and metrics
├── backend/
│   ├── data/                     # All generated artifacts (git-ignored except queries)
│   │   ├── raw/LK-Hadith-Corpus/ # Cloned LK source (git-ignored)
│   │   ├── (corpus, index and embeddings are in PostgreSQL)
│   │   ├── queries.json          # 20 evaluation queries (committed)
│   │   ├── training_queries.json # 100 training queries (committed)
│   │   ├── dropped_lk_rows.json  # Drop audit (git-ignored, generated)
│   │   └── build_manifest.json   # Build provenance (git-ignored, generated)
│   ├── models/
│   │   └── schemas.py            # Pydantic request/response schemas
│   ├── routers/
│   │   ├── search.py             # /api/v1/searches, /search-methods
│   │   ├── annotation.py         # /api/v1/assignments, /agreement (token required)
│   │   ├── benchmark.py          # /api/v1/benchmark
│   │   ├── auth.py               # /api/v1/annotators, /tokens
│   │   └── kv_pairs.py           # /api/v1/kv-pairs
│   ├── scripts/
│   │   ├── build_all.py          # Canonical build orchestrator — run this
│   │   ├── data_creation.py      # LK loader, reconstruction, DB creation
│   │   ├── profile.py            # Read-only corpus audit
│   │   ├── preprocess.py         # Text preprocessing (6 columns)
│   │   ├── build_inverted_index.py  # BM25 sparse index
│   │   ├── build_embeddings.py   # Arabic dense embeddings (ONNX)
│   │   ├── pooling.py            # Candidate pool for annotation
│   │   ├── search.py             # All 11 retrieval algorithms
│   │   ├── loading.py            # LRU-cached loaders for indexes/models
│   │   ├── evaluation.py         # IR metrics (P@k, R@k, nDCG@k, AP, MRR)
│   │   ├── full_evaluation.py    # Baseline vs fine-tuned comparison orchestrator
│   │   ├── finetune.py           # LoRA fine-tuning (3 modes)
│   │   ├── finetune_eval.py      # Re-encode + evaluate a fine-tuned adapter
│   │   ├── llm_grader.py         # LLM-based relevance grading
│   │   ├── llm_validation.py     # Kappa/Spearman LLM vs human agreement
│   │   ├── kv_generator.py       # Concept-entity pair generation
│   │   ├── stats_tests.py        # Paired t-test, Wilcoxon, bootstrap CI, LaTeX
│   │   └── pooling.py            # Retrieval pool for annotation
│   ├── database.py               # PostgreSQL engines, sessions and schema init
│   └── main.py                   # FastAPI app, APP_MODE, lifespan
├── frontend/
│   └── src/
│       ├── pages/                # React page components
│       ├── components/           # Shared UI components
│       ├── api/                  # API client + AuthContext
│       └── i18n/                 # English/Arabic translations
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── docker-entrypoint.sh
```

---

## 4. Data Pipeline — Overview

Run the full pipeline from `backend/`:

```powershell
python scripts\build_all.py --force
```

Skip embeddings and pooling (for local dev):

```powershell
python scripts\build_all.py --force --skip-embeddings
```

**Pipeline steps in order**:

| Step | Script | Input | Output |
|------|--------|-------|--------|
| 1 | `data_creation.py` | LK-Hadith-Corpus CSVs | `hadiths` table, `dropped_lk_rows.json` |
| 2 | `profile.py` | `hadiths` table | Console audit report (read-only) |
| 3 | `preprocess.py` | `hadiths` table | 6 `Preprocessed_*` columns in the `hadith_preprocessed` table |
| 4 | `build_inverted_index.py` | `hadiths` table | `terms`, `postings`, `hadith_lengths` tables |
| 5 | `build_embeddings.py` | `hadiths` table | `hadith_embeddings` table |
| 6 | `pooling.py` | All of the above | `qrels_ungraded.json` |

`build_all.py` writes `build_manifest.json` at the end recording the LK source commit SHA, corpus size, and artifact checksums.

---

## 5. Corpus

**Source**: LK Hadith Corpus (6 canonical Sunni collections)  
**Canonical size**: 33,064 rows (the loaded `hadiths` table; 33,491 is the size after the first-stage drop, before the second)  
**Schema**: See the `hadiths` table (`backend/models/orm.py`) — key columns:

| Column | Description |
|--------|-------------|
| `id` | Auto-increment primary key |
| `Book` | One of: Bukhari, Muslim, AbuDaud, Nesai, IbnMaja, Tirmizi |
| `Hadith_Number` | LK-native hadith number within book |
| `Chapter_Number` (titles in the `chapters` table) | Chapter metadata |
| `English_Text`, `Arabic_Text` | Full hadith text (isnad + matn) |
| `English_Isnad`, `Arabic_Isnad` | Narrator chain |
| `English_Matn`, `Arabic_Matn` | Hadith body text |
| `English_Grade`, `Arabic_Grade` | Raw grade strings from LK |
| `Normalized_Grade` | Normalized grade: Sahih / Hasan / Da'if / Mawdu / Unknown |
| `English_Matn_Source`, `Arabic_Matn_Source` | `lk_original` or `reconstructed` |
| `Has_English_Matn`, `Has_Arabic_Matn` | Boolean content flags |
| `Preprocessed_English`, `Preprocessed_Arabic` | Full text, preprocessed (table `hadith_preprocessed`) |
| `Preprocessed_English_Isnad`, `Preprocessed_Arabic_Isnad` | Isnad, preprocessed |
| `Preprocessed_English_Matn`, `Preprocessed_Arabic_Matn` | Matn, preprocessed — **primary retrieval field** |

**All corpus construction and filtering decisions are documented in `docs/CORPUS_DECISIONS.md`.**

---

## 6. Preprocessing

**File**: `backend/scripts/preprocess.py`

Two independent pipelines — one for English, one for Arabic.

**English pipeline**:
1. Lowercase + remove all non-alpha characters (regex)
2. NLTK `word_tokenize`
3. WordNet lemmatization with POS tagging
4. Remove NLTK stopwords + custom isnad stopwords

**Arabic pipeline**:
1. Strip non-Arabic Unicode characters (regex)
2. Normalize alef variants, hamza, teh marbuta, tatweel (CAMeL Tools)
3. `simple_word_tokenize`
4. CAMeL MLE morphological disambiguation (calima-msa-r13)
5. Extract lemma from best analysis; fall back to raw diacritics-stripped token if no analysis
6. Filter by POS (remove prep, conj, part, punc)
7. Remove custom Arabic isnad stopwords

Both pipelines are run over three text fields (full text, isnad, matn) independently, producing six preprocessed columns. Only the matn columns are used for retrieval; the others exist for future experiments.

---

## 7. Retrieval Systems

**File**: `backend/scripts/search.py`

11 retrieval systems are implemented. All operate over the same canonical corpus.

| System | Type | Description |
|--------|------|-------------|
| Term Overlap | Sparse | Exact term match, ranked by TF |
| TF-IDF | Sparse | Classic TF × IDF weighting |
| BM25 | Sparse | Okapi BM25 (tuned k1, b) |
| BM25 + TF-IDF Hybrid | Sparse | Weighted fusion of BM25 and TF-IDF scores |
| BM25 + PRF | Sparse | BM25 with pseudo-relevance feedback query expansion |
| BM25 + TF-IDF + PRF | Sparse | Hybrid with PRF |
| Cosine Similarity | Dense | Arabic embeddings, cosine similarity (Arabic queries only) |
| Semantic Rerank | Dense | BM25 candidates reranked by cosine similarity |
| Semantic RRF | Dense+Sparse | Reciprocal Rank Fusion of BM25 and cosine |

**Dense retrieval details**:
- Model: `masterofaudio2077/Fada_ar_embedding`, exported to ONNX, vectors cut to 64 dimensions
- No query or passage prefix
- Passages are the Arabic matn with diacritics removed. Queries are cleaned the same way.

**Sparse retrieval details**:
- Index: `terms`, `postings`, `hadith_lengths` tables
- Input field: `Preprocessed_English_Matn` / `Preprocessed_Arabic_Matn` only
- Language detection: query is routed to the appropriate index by script

---

## 8. Fine-Tuning

**Full details**: `docs/FINE_TUNING.md`

Three LoRA adapters are trained on top of `intfloat/multilingual-e5-large`. The goal is to adapt E5 to the hadith domain and determine empirically which training signal produces the best retrieval improvement.

**Quick reference**:

| Mode | Training data source | Key file |
|------|---------------------|----------|
| `triplet` | `training_qrels_graded.json` (LLM-graded) | `finetune.py` |
| `kv_pairs` | `kv_pairs_verified.json` (concept-entity) | `finetune.py` |
| `combined` | Both merged | `finetune.py` |

**Hyperparameters** (all overridable via env vars):

| Parameter | Default | Env var |
|-----------|---------|---------|
| LoRA rank | 16 | `LORA_R` |
| LoRA alpha | 32 | `LORA_ALPHA` |
| LoRA dropout | 0.1 | `LORA_DROPOUT` |
| Batch size | 16 | `FINETUNE_BATCH_SIZE` |
| Learning rate | 2e-5 | `FINETUNE_LR` |
| Max epochs | 20 | `FINETUNE_EPOCHS` |
| Early stopping patience | 3 | `FINETUNE_PATIENCE` |
| Val split | 15% | `FINETUNE_VAL_SPLIT` |
| MNRL temperature | 0.05 | `MNRL_TEMPERATURE` |

**Loss**: Multiple Negative Ranking Loss (MNRL) with in-batch negatives.  
**Train/val split**: 85/15, fixed seed (42).  
**Output**: `backend/data/finetuned/{mode}/` containing the adapter weights and `training_history.json`.

---

## 9. Evaluation

**Full details**: `docs/EVALUATION.md`

**Evaluation queries**: 20 queries in `backend/data/queries.json` — 10 English, 10 Arabic.

**Metrics** (all at k=20):
- Precision@k, Recall@k
- nDCG@k
- Average Precision (AP) → MAP
- Mean Reciprocal Rank (MRR)
- F1@k

**Relevance judgments**: Human-annotated via the annotation platform. Each query is assigned to 3 annotators. Inter-annotator agreement is computed with Cohen's Kappa.

**Statistical significance**: Paired t-test, Wilcoxon signed-rank test, and bootstrap confidence intervals (1000 iterations) via `stats_tests.py`.

**Outputs**:
- `qrels_results.json` — per-query, per-system metric scores
- `stats_results.json` — significance test results
- `results_table.tex` — LaTeX table for paper
- `comparison_table.tex` — baseline vs fine-tuned comparison
- `delta_table.tex` — relative improvement percentages

---

## 10. Annotation Platform

**File**: `backend/routers/annotation.py`, `backend/database.py`

The annotation platform is a web UI where human annotators rate hadith relevance for each query.

**Access**: Deployed with `APP_MODE=annotation`. This disables heavy model imports (the Arabic encoder, BM25 loaders) so the app runs on ~200MB RAM.

**Flow**:
1. Annotator signs up → account created, query assignments auto-generated
2. Annotator works through assigned queries, grading each pooled hadith as relevant (1) or not (0)
3. Grades are stored per-annotator in PostgreSQL
4. The annotation router exports merged qrels and computes inter-annotator Kappa on demand

**Pooling** (`pooling.py`): Candidates are the union of top-50 results from five selected systems: BM25, BM25_ROCCHIO, COSINE_SIMILARITY, BM25_SEMANTIC_RERANK and BM25_RRF. Outputs: `qrels_ungraded.json` and `pooling_manifest.json`, including contribution counts and failures. A failed contributor blocks pool export unless partial pooling is explicitly enabled.

**Important**: If the corpus changes (new `hadiths.db`), existing annotation assignments reference stale hadith IDs. Annotation must be restarted from scratch after any corpus rebuild.

---

## 11. LLM Grading Pipeline

**Files**: `backend/scripts/llm_grader.py`, `backend/scripts/llm_validation.py`

**Purpose**: Generate relevance judgments for the 100 training queries automatically, without requiring human annotation for training data.

**Two modes**:
- `--validate`: Grades the 20 human eval queries with the LLM, then compares to human judgments to measure agreement. This validates that LLM grades are reliable enough to use for training.
- Default: Grades all 100 training queries. Output: `training_qrels_graded.json`.

**Agreement threshold**: The paper requires Cohen's Kappa ≥ 0.40 (moderate agreement) to justify using LLM grades as training signal.

**Configuration** (env vars): `LLM_API_BASE`, `LLM_API_KEY`, `LLM_MODEL` — compatible with any OpenAI-compatible endpoint (GPT-4o, Ollama, etc.).

---

## 12. KV Pair Generation

**Files**: `backend/scripts/kv_generator.py`, `backend/routers/kv_pairs.py`

**Purpose**: Generate (concept, hadith) training pairs that teach E5 Islamic domain vocabulary it likely did not learn during general pretraining.

**Rationale**: A query like "the ruling on music and singing" uses domain-specific Islamic fiqh framing. General E5 may not associate this framing with the relevant hadiths. KV pairs are generated by extracting key Islamic concepts from hadith text using an LLM, then linking those concepts to the source hadiths. Fine-tuning on these pairs aligns the embedding space to Islamic terminology.

**Generation flow**:
1. LLM extracts concept-entity pairs from hadith matn text
2. Generated pairs are stored in PostgreSQL
3. Human verifier uses the web UI (`/dev/kv-pairs`) to mark pairs as verified or rejected
4. Verified pairs are exported to `kv_pairs_verified.json`

**Scale**: Target ~1000 verified pairs across 10 Islamic topics.

---

## 13. Backend API

**File**: `backend/main.py`

**APP_MODE** (env var): Controls which routers and model loaders are initialized at startup.

| Mode | Loaded | Use case |
|------|--------|----------|
| `annotation` | Auth, annotation routers only | Annotation deployment — no heavy model imports |
| `search` (default) | All routers, Arabic encoder lazy-loaded | Development and production search |
| Any other / unset | All routers | Full system |

**Key env vars**:

| Variable | Purpose |
|----------|---------|
| `LK_HADITH_CORPUS_PATH` | Override path to LK clone |
| `APP_MODE` | Control which app features load |
| `FINETUNED_ADAPTER_PATH` | Path to a LoRA adapter to load for search |
| `LLM_API_BASE`, `LLM_API_KEY`, `LLM_MODEL` | LLM grader configuration |
| `VITE_API_BASE_URL` | Frontend API base URL (build-time) |

**Routers**:
All routes live under `/api/v1`. `GET /api/v1` lists them as links. Errors use `application/problem+json`.

- `GET /searches?q=&method=&lang=&grade_filter=&book_filter=` — one of the 11 retrieval systems; `GET /search-methods` lists the enabled ones. Cached for 5 minutes, with an ETag.
- `GET /hadiths/{id}` — one hadith, cached for an hour.
- `GET /assignments`, `GET /assignments/{query_id}` — the signed-in annotator's queries. `PUT /assignments/{query_id}/labels/{hadith_id}` saves a label (201 the first time, 200 after) and `PUT /assignments/{query_id}/progress` saves the cursor. `GET /agreement` gives inter-annotator agreement.
- `POST /annotators` signs up, `POST /tokens` signs in, `GET /annotators/me` reads the profile. Tokens are signed JWTs sent as `Authorization: Bearer <token>`.
- `GET /benchmark/{results,stats,qrels,finetuned,finetuned-stats,comparison}` — evaluation output; 404 if the file has not been built.
- `GET /kv-pairs` (paged, `?status=`), `GET /kv-pairs/statistics`, `PATCH /kv-pairs/{id}` and `PATCH /kv-pairs` (batch) with `{"status": "verified" | "rejected"}`.

Running several servers behind a load balancer: give every one the same `AUTH_SECRET` (32+ characters) and the same database. Nothing else is kept in server memory between requests.

---

## 14. Frontend

**Directory**: `frontend/src/`

React + TypeScript + Tailwind CSS + Vite SPA.

**Key pages**:
- Search page — bilingual hadith search
- Annotation page — relevance judgment UI for annotators
- Annotation guidelines page — instructions for annotators
- KV verification page — concept-entity pair verification UI
- Sign in / Sign up pages

**i18n**: Full English/Arabic UI translations in `frontend/src/i18n/translations/`.

**API base URL**: Set via `VITE_API_BASE_URL` at build time. Defaults to the dev server proxy.

---

## 15. Deployment

**See**: `DEPLOYMENT_GUIDE.md` (repo root) for full Dokploy deployment instructions.

**Summary**:
- Docker-based deployment
- Port 8000 is nginx (rate limit on sign-in/sign-up), which proxies to the app; the app serves both the API and the built frontend files
- Data volume must be mounted at `/app/backend/data` in the container
- For annotation-only deployment: `APP_MODE=annotation` — minimal RAM (~200MB)
- For full search deployment: the Arabic encoder loads lazily on first dense query (about 0.7 GB RAM)

---

## 16. Open Questions and Known Issues

| Issue | Status | Notes |
|-------|--------|-------|
| Second-stage drop not yet implemented | Pending | ~427 rows with empty preprocessed matn need to be dropped; pipeline currently fails with `ValueError` at preprocess step |
| Annotation must be restarted | Blocked | Depends on corpus rebuild completing |
| Only 4/20 queries have human judgments | Active bottleneck | Annotation is the critical path for the paper |
| `training_qrels_graded.json` not yet generated | Pending | Needs LLM endpoint |
| `kv_pairs_verified.json` not yet generated | Pending | Needs LLM generation + manual verification |
| LoRA adapters not yet trained | Pending | Needs GPU |
| Embeddings not yet generated | Pending | Needs GPU; blocked on preprocessing passing |
| Pooling not yet run | Pending | Blocked on embeddings |
| `build_manifest.json` references old corpus size | Stale | Will be corrected after pipeline rerun |

---

## 17. Key Invariants — Never Break These

These are design constraints that must be maintained for the system to function correctly. Any change that would violate these requires explicit discussion.

1. **English and Arabic indices must cover exactly the same set of hadith IDs.** `hadith_lengths` holds both lengths on one row, and `build_inverted_index.py` refuses to run if any hadith has an empty matn in either language.

2. **Retrieval fields are matn-only for both sparse and dense systems.** Isnad tokens must not appear in `Preprocessed_English_Matn` or `Preprocessed_Arabic_Matn`. Custom stopwords handle this.

3. **Embeddings are keyed by hadith id, not by position.** `hadith_embeddings` has one row per hadith with a foreign key to `hadiths.id`, so there is no array alignment to keep. Every vector in a column must come from the same model.

4. **The corpus must be rebuilt deterministically.** No random sampling during corpus construction. Reconstruction rules are rule-based only. The LK source commit SHA is recorded for reproducibility.

5. **`APP_MODE=annotation` must not import the Arabic encoder or BM25 loaders.** The annotation deployment environment does not have enough RAM. Routers that depend on search/benchmark functionality are conditionally loaded in `main.py`.

6. **Evaluation must use human-annotated qrels, not LLM-graded ones.** LLM grades are used only for the training data (`training_qrels_graded.json`). The 20 eval queries use `qrels_graded.json` from human annotators. These must not be mixed.
