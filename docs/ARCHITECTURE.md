# System Architecture

> Technical reference for the retrieval pipeline architecture, data flow, and component interactions.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Client Browser                        │
│                  React SPA (port 5173 dev)                   │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTP (REST, /api/v1)
┌────────────────────────────▼────────────────────────────────┐
│                    FastAPI Backend (port 8000)                │
│                                                              │
│  ┌──────────┐  ┌────────────┐  ┌────────────┐  ┌────────┐  │
│  │ /searches│  │/assignments│  │/benchmark  │  │/tokens │  │
│  └────┬─────┘  └─────┬──────┘  └─────┬──────┘  └────────┘  │
│       │              │               │                       │
│  ┌────▼──────────────▼───────────────▼──────────────────┐   │
│  │       services/retrieval.py + services/ranking.py      │   │
│  │           8 retrieval systems                         │   │
│  └───────────┬──────────────────────┬────────────────────┘   │
│              │                      │                        │
│  ┌───────────▼──────┐  ┌────────────▼────────────────────┐  │
│  │  Sparse Index    │  │  Dense Index                    │  │
│  │  BM25 / TF-IDF   │  │  Arabic embeddings (pgvector)   │  │
│  │  (postings SQL)  │  │                                 │  │
│  └───────────┬──────┘  └────────────┬────────────────────┘  │
│              │                      │                        │
│  ┌───────────▼──────────────────────▼────────────────────┐  │
│  │                  PostgreSQL + pgvector                 │  │
│  │   33,064 rows × bilingual matn-complete corpus         │  │
│  └────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Build Pipeline Data Flow

```
LK-Hadith-Corpus/ (CSVs)
        │
        ▼
data_creation.py
  ├── Load 6 books → concatenate (~34,088 rows)
  ├── Reconstruct missing matn/isnad fields (deterministic)
  ├── Drop rows missing bilingual matn (first-stage drop)
  │     └── 597 rows dropped → dropped_lk_rows.json
  ├── Normalize grades
  └── Write → books, chapters, hadiths tables (33,491 hadiths at this step)
        │
        ▼
profile.py (read-only audit, no modifications)
        │
        ▼
preprocess.py
  ├── Preprocess English text, isnad, matn (3 columns)
  ├── Preprocess Arabic text, isnad, matn (3 columns)
  ├── Detect rows where preprocessed matn is empty (second-stage drop candidates)
  └── Write the 6 Preprocessed_* columns → hadith_preprocessed table
        │
        ▼
build_inverted_index.py
  ├── Read Preprocessed_English_Matn, Preprocessed_Arabic_Matn
  ├── Build BM25 postings lists
  └── Replace rows in terms, postings, hadith_lengths
        │
        ▼
build_embeddings.py
  ├── Read Arabic_Matn
  ├── Remove diacritics and extra spaces (encoding_text), no prefix
  ├── Encode with the ONNX export of masterofaudio2077/Fada_ar_embedding (ONNX Runtime, CPU)
  └── Upsert 64-dimension float32 vectors into hadith_embeddings.arabic
        │
        ▼
pooling.py
  ├── Run all retrieval systems over 20 eval queries
  ├── Union of top-k results per query
  └── Write → qrels_ungraded.json
```

---

## Retrieval Data Flow (At Query Time)

```
User query (text string + language)
        │
        ▼
routers/search.py
  ├── Detect language (EN / AR)
  ├── Pick the system in services/retrieval.py and run it (services/ranking.py)
  │
  ├── Sparse path:
  │     ├── preprocess_english(query) or preprocess_arabic(query)
  │     ├── Look up the terms in the `terms` and `postings` tables
  │     └── Score with BM25 / TF-IDF / Overlap
  │
  └── Dense path:
        ├── Arabic only (English queries get HTTP 422)
        ├── Clean the query with encoding_text and encode it (loading.py LRU cache)
        └── Cosine distance against the embeddings in PostgreSQL (pgvector)
                │
                ▼
        Top-k hadith IDs → fetch from the hadiths table → return JSON
```

---

## Loading and Caching (`loading.py`)

Only the NLP and model objects are loaded lazily and cached with `functools.lru_cache`. The index and the embeddings are database tables, queried per request (`services/ranking.py`).

| Loader | Object | Trigger |
|--------|--------|---------|
| `get_model()` | ONNX Runtime session (Arabic encoder) | First dense search |
| `get_mle()` | CAMeL MLE disambiguator | First Arabic preprocessing |
| `get_english_lemmatizer()` | NLTK WordNetLemmatizer | First English preprocessing |

**Model files**: `get_model()` reads `model.onnx` and `tokenizer.json` from `backend/data/onnx/arabic` (override with `ARABIC_MODEL_DIR`). If they are missing it says to run `python -m scripts.export_onnx`.

---

## Database Schema (PostgreSQL)

The source of truth is `backend/models/orm.py`; the picture is [`schema.svg`](diagrams/schema.svg) (from [`schema.dbml`](diagrams/schema.dbml)).

The schema is in third normal form (HANDOFF item 28):

| Table | Primary key | Holds |
|-------|-------------|-------|
| `books` | `book` | the six collections and their short LK names |
| `chapters` | `(book, chapter_number)` | chapter titles in English and Arabic |
| `hadiths` | `id` | the bilingual corpus row; `Book` and `(Book, Chapter_Number)` are foreign keys. Section columns stay here because `(Book, Section_Number)` is not a key |
| `hadith_preprocessed` | `hadith_id` | the six `Preprocessed_*` texts, the input of the BM25 build |
| `hadith_embeddings` | `hadith_id` | one Arabic vector per hadith (`arabic`, 64 dimensions) |
| `hadith_lengths` | `hadith_id` | token counts of the preprocessed matn |
| `terms`, `postings` | `(language, term)`, `(language, term, hadith_id)` | the BM25 inverted index |
| `annotators`, `assignments`, `annotations`, `annotation_progress` | see the ORM | the annotation platform |
| `kv_pairs` | `id` | concept and entity pairs; the hadith text is read through `hadith_id` |

Indexes on `hadiths`: `Book` and `Normalized_Grade`.

---

## Alignment Invariant

Embeddings, postings and lengths are keyed by `hadith_id` with a foreign key to `hadiths.id` and `ON DELETE CASCADE`, so they cannot point at a hadith that is gone. Changing the corpus (rows added or removed) means re-running `build_inverted_index.py` (its `write_index` replaces the index rows) and `build_embeddings.py` (it upserts vectors, so rows for new hadiths are added; rows for removed ones are deleted with the hadith). `hadith_embeddings` has no fixed vector dimension, so switching the embedding model needs no migration, but every row must be re-encoded with the same model.
