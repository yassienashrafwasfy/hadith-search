# Hadith Search Engine

A full-stack hadith search engine supporting both English and Arabic queries with multiple ranking algorithms.

## Prerequisites

- Python 3.10+
- Node.js 18+
- CUDA-capable GPU (recommended for embedding generation — CPU fallback available but very slow)

## Setup

```bash
# Backend
python -m venv venv
# On Windows: venv\Scripts\activate
# On Linux/Mac: source venv/bin/activate
cd backend
pip install -r ..\requirements.txt
mkdir data
```

### External Data Downloads

These must be run **after** `pip install`:

```bash
# NLTK data (tokenization, stopwords, lemmatization, POS tagging)
python -m nltk.downloader punkt_tab stopwords wordnet averaged_perceptron_tagger averaged_perceptron_tagger_eng

# CAMeL Tools data (calima-msa-r13 MLE disambiguator + morphology DB)
camel_data -i disambig-mle-calima-msa-r13
```

### Environment Variables

No API keys are needed for search or the build pipeline. Copy `.env.example` to `.env` and set `DATABASE_URL` (see the file for the other settings).

### Frontend

```bash
cd frontend
npm install
```

## Build Pipeline

Run all steps in sequence with a single command:

```bash
cd backend
python scripts\build_all.py
```

For local development without regenerating dense embeddings:

```bash
cd backend
python scripts\build_all.py --skip-embeddings
```

The script will:
1. Check if each step's output already exists and prompt you to overwrite, skip, or quit
2. Run each step with timing and error reporting
3. Stop on the first failure
4. Write `backend/data/build_manifest.json` with source, field, and artifact metadata

Use `--force` to rebuild existing outputs without prompts. By default the full pipeline includes dense embeddings and pooling. `--skip-embeddings` skips both embedding generation and pooling.

### Individual Steps

You can also run steps individually:

| Step | Command | Description | Output |
|------|---------|-------------|--------|
| 1 | `python scripts\data_creation.py` | Loads LK Hadith Corpus, applies deterministic reconstruction, and keeps the bilingual matn-complete corpus | `hadiths` table in PostgreSQL |
| 2 | `python scripts\profile.py` | Profiles the loaded corpus without modifying it | Console report |
| 3 | `python scripts\preprocess.py` | Tokenizes, lemmatizes, and removes stopwords for full text, isnad, and matn | Adds/updates six `Preprocessed_*` columns |
| 4 | `python scripts\build_inverted_index.py` | Builds the BM25 index over matn only | `terms`, `postings` and `hadith_lengths` tables |
| 5 | `python scripts\build_embeddings.py` | Generates Arabic dense embeddings with the ONNX encoder over the matn only (first run `python -m scripts.export_onnx` once to create the model) | `hadith_embeddings` table (float32 vectors) |
| 6 | `python scripts\pooling.py` | Pools candidate documents for relevance judgment | `qrels_ungraded.json` |

> **Note:** Step 5 requires significant VRAM (~12GB recommended). If you don't have a GPU, the script will prompt you before falling back to CPU (extremely slow).

### Download Pre-built Embeddings

If you want to skip the embedding generation step, download the pre-built files from [this link](PLACEHOLDER_URL) and extract them into `backend/data/`.

## Running the Project

### Backend

```bash
cd backend
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm run dev
```

The frontend runs at `http://localhost:5173` by default.

## Project Structure

```
hadith-search/
├── backend/
│   ├── data/                 # Raw LK cache, DB, embeddings, indices, qrels, manifest
│   ├── routers/              # FastAPI endpoints
│   ├── scripts/              # Data processing & indexing scripts
│   │   ├── build_all.py          # Combined build orchestrator
│   │   ├── data_creation.py      # Load LK corpus & create bilingual matn DB
│   │   ├── profile.py            # Corpus profiling report
│   │   ├── preprocess.py         # Full text/isnad/matn preprocessing
│   │   ├── build_inverted_index.py  # BM25 inverted index builder
│   │   ├── build_embeddings.py   # Dense embedding generator
│   │   ├── search.py             # Search algorithms
│   │   ├── loading.py            # Data loading utilities
│   │   ├── pooling.py            # Relevance judgment pooling
│   │   ├── evaluation.py         # IR evaluation metrics
│   │   └── ...
│   └── main.py               # FastAPI application
├── frontend/
│   ├── src/
│   │   ├── components/       # React components
│   │   ├── pages/            # Page components
│   │   ├── api/              # API client
│   │   └── i18n/             # Internationalization
│   └── package.json
├── requirements.txt
└── README.md
```

## Available Scripts

### Data Processing
- `build_all.py` - Run the full build pipeline (recommended)
- `build_embeddings.py` - Generate dense embeddings (requires CUDA, with CPU fallback prompt)
- `build_inverted_index.py` - Build the BM25 index in PostgreSQL
- `data_creation.py` - Load LK Hadith Corpus into PostgreSQL
- `profile.py` - Profile the loaded corpus without mutating it
- `preprocess.py` - Preprocess hadith text
- `pooling.py` - Pool candidate documents for relevance judgment
- `evaluation.py` - Compute IR evaluation metrics

## Retrieval Architecture

| Algorithm | Description |
|-----------|-------------|
| **Term Overlap** | Exact matches ranked by term frequency |
| **TF-IDF** | Classic term frequency-inverse document frequency ranking |
| **BM25** | Okapi BM25 with optimized k1 and b parameters |
| **BM25 + TF-IDF (Hybrid)** | Combines BM25 and TF-IDF scores with weighted fusion |
| **BM25 + PRF** | BM25 with pseudo-relevance feedback (query expansion) |
| **Cosine Similarity** | Dense retrieval using vector embeddings and cosine similarity |
| **Semantic Rerank** | BM25 candidates reranked by cosine similarity |
| **Semantic RRF** | Reciprocal Rank Fusion combining sparse (BM25) and dense (cosine) results |

### Search Architecture
- **Sparse Retrieval**: BM25, TF-IDF, Term Overlap — Fast, interpretable, language-independent
- **Dense Retrieval**: Cosine similarity over Arabic embeddings from `masterofaudio2077/Fada_ar_embedding` (ONNX, 256 dimensions). Arabic queries only
- **Fusion**: Reciprocal Rank Fusion (RRF) for combining multiple retrieval methods

## Tech Stack

- **Backend**: FastAPI, PostgreSQL + pgvector, NLTK, CAMeL Tools, ONNX Runtime
- **Frontend**: React, TypeScript, Tailwind CSS, Vite
- **Retrieval**: BM25, BM25+PRF, Dense retrieval (Arabic only), Hybrid

## Deploying

Releases run blue/green behind nginx on one host, with optional canaries by client address.
Set `POSTGRES_PASSWORD` and `AUTH_SECRET` in `.env`, then:

```bash
tools/deploy.sh init              # first time: postgres, nginx, blue
tools/deploy.sh deploy --build    # new version on the idle colour
tools/deploy.sh canary 10         # 10% of client addresses
tools/deploy.sh promote           # or: tools/deploy.sh rollback
```

Both colours share one database, so schema changes must be additive. Details are in `docs/HANDOFF.md`, item 21.
