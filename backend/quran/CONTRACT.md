# Quran RAG: contract and file ownership

Plan: /home/yaso/.claude/plans/velvet-wishing-puzzle.md (read it first). Shared types are in
`types.py` (already written; ask the owner before changing). Flag `quran_rag` and the `quran_*`
settings already exist. Postgres for tests: `TEST_DATABASE_URL` default localhost:55432 is running.

Rules: ORM only (no raw SQL strings); functions complexity <= 10; ruff + black (line 100) +
vulture clean; fakes injected (LLMClient, Encoder), no network or model in tests; Tanzil text is
stored byte-exact, search text goes in a separate column; additive schema only; do NOT commit,
do NOT edit files you do not own (message the owner via SendMessage instead); do not edit
requirements*.txt except the api agent. Tests go in `tests/quran/` (own file per agent).

| Agent | Owns | Provides |
|---|---|---|
| schema | quran/models.py, quran/db.py, tests/quran/conftest.py | `node_table(source)` factory (one PG schema per source: id, parent_id, level, text, search_text, summary, embedding VECTOR, surah, ayah_from, ayah_to, juz, place, author), `Source` catalog ORM table, `create_source(session, SourceInfo)`, `list_sources(session)`, tiny 3-surah fixture + fake encoder fixtures |
| loader | quran/loader.py, scripts/quran_load.py | parse Tanzil text + metadata and the tafsir SQLite into `RawAyah`/`RawTafsir` dataclasses, insert ayah rows (level=ayah) into a source store; LICENSES.md writer |
| chunking | quran/chunking.py | `semantic_passages(ayat, vectors, ...) -> list[range]` pure function; never splits an ayah; min/max token bounds |
| ingest | quran/ingest.py, quran/summarize.py, scripts/quran_build.py | chain: tokenise -> chunk -> embed (Encoder) -> summarise (LLMClient) -> write passage and surah nodes with parent_id |
| llm | quran/llm.py | `LlamaServerClient(base_url, timeout)` implementing LLMClient (httpx, JSON-schema constrained), `FakeLLM` for tests, raises LLMError |
| retrieval | quran/retrieval.py | `search_source(session, source, qvec, filters, k) -> list[NodeRef]` (cosine over ayah leaves, filters before top-k) and `auto_merge(session, hits, fraction) -> list[NodeRef]` returning ORIGINAL text, plus `get_passage(session, source, surah, a, b)` |
| selfquery | quran/selfquery.py, quran/router.py | `extract_filters(llm, question, source_info) -> Filters` (pydantic-validated, unknown values dropped), `choose_source(llm, question, sources) -> str or None` |
| web | quran/web.py | `AllowListedWeb.search(query) -> list[WebPassage]`; HTTPS, exact host allow-list, off-list redirect rejected, size/time limits, injectable httpx transport |
| agent | quran/agent.py, quran/grounding.py | LangGraph graph route->retrieve->draft->critique->{revise,web_search,finalize,fallback}, max loops from settings; `render(citation_ids, retrieved)` inserts verbatim text; every failure -> `FALLBACK_SENTENCE`; takes dependencies via a `Deps` dataclass |
| api | routers/quran.py, routers/__init__.py, routers/root.py, main.py, quran/limiter wiring (reuse limiter.py), nginx/default.conf, docker-compose.yml, requirements.txt (langgraph), docs/HANDOFF.md new item, docs/behaviours/quran_answers.feature + tests/bdd steps | `POST /api/v1/answers`, 503 + Retry-After via its own `SearchLimiter`, root link, nginx location with limit_req |

Communication: message teammates by name (schema, loader, chunking, ingest, llm, retrieval,
selfquery, web, agent, api) with the exact function signature you need or changed. Keep
messages short. Report to `main` when done: files written, test result, open issues.
