# Handoff: changes since Marawan's last commit

Marawan's last commit is `93a4ff9` ("Align matn embeddings, training passages, and evaluation"). Everything below was added on top of it in 48 commits (39 on 2026-09-30, 9 on 2026-10-01; items 22 to 24 are the newest, the others are the REST API, items 16 and 17, the security pass, item 18, nginx with the sign-in limit, item 19, PostgreSQL with pgvector, item 20, and blue/green and canary releases, item 21): about 140 files. All 431 tests pass (2 skipped: one needs NLTK data, one needs the exported Arabic model). The tests need a PostgreSQL with pgvector, see item 20. The work sits on the branch `feat/blue-green-canary`, which builds on `feat/postgres-pgvector`, `feat/rest-api-v1`, `chore/dockerfile-hardening` and `chore/precommit-hooks`, and has not been pushed.

Each change has the same three lines: which files, why this is the normal way to do it, and what you get out of it.

## What changed when

Newest first. The numbers in brackets are the items below.

| Date | Change |
| --- | --- |
| 2026-10-01 | Load testing and performance, 20 users on a 2 vCPU / 4 GB container: 3.0 to 14.4 requests a second, p95 12 s to 0.36 s [24]. Cold-start memory crash fixed, unused columns no longer loaded, ONNX encoder limited to 1 thread (`ARABIC_ENCODER_THREADS`), search response serialised once [24]. |
| 2026-10-01 | Recall@3 and @8 of the three Arabic semantic methods, measured with two proxy tests [23]. |
| 2026-10-01 | Method picker updates as soon as the search language changes; Arabic encoder batches texts by length [23]. |
| 2026-09-30 | Arabic ONNX semantic ranker replaces multilingual E5; the Jina reranker is removed [22, 23]. |
| 2026-09-30 | Blue/green releases and canaries, nginx with a sign-in limit, PostgreSQL with pgvector [19, 20, 21]. |
| 2026-09-30 | Security pass, signed tokens, REST API under `/api/v1` [16, 17, 18]. |
| 2026-09-30 | Async ORM, tests, pre-commit hooks, hardened Docker image and scans [1 to 15]. |

## Read this first

1. **Semantic search is Arabic only and runs on an ONNX model** (item 23). Export the model and build the embeddings before turning `FEATURE_DENSE_RETRIEVAL` on.
2. **The Jina reranker is gone** (item 22). `JINA_API_KEY` is no longer read, so you can delete it from your `.env` and server settings.
3. **The database (now PostgreSQL, item 20) enforces foreign keys.** Links between tables are declared in the models and Postgres blocks a bad link and cascades deletes. Tables are created with `create_all`: it adds missing tables and never changes existing ones, and there are no migrations.
4. **The diff looks bigger than the real change.** Every Python file was reformatted to one style, so many lines only moved or wrapped differently. Read the commit messages first, then the files.
5. **Libraries were upgraded** to fix known security holes: numpy 1.26 to 2.5, transformers 4.43 to 5.17, starlette 0.52 to 1.3, nltk 3.9 to 3.10. The tests pass, but I could not test saved index files (`.pkl`) built with the old numpy, because this copy has no real data. If loading fails, rebuild them. A full fine-tuning run and the real E5 model were also not re-run.
6. **Every API URL changed, and there are no old aliases.** Everything now lives under `/api/v1`, the frontend is updated, and anything else that calls the API (scripts, bookmarks) must move. The map is in item 16 below. Sign-in also works differently: set `AUTH_SECRET` (32+ characters) in `.env`, and use the same value on every server.
7. **Passwords must now be 8 to 128 characters, and `/kv-pairs` needs a login.** Anything that called `/kv-pairs` without a token now gets 401.
8. **The Docker container no longer runs as root.** It runs as user 10001. If you mount a folder for `backend/data` instead of using a Docker volume, run `chown 10001 <folder>` on it once.

`uvicorn main:app` still starts the server the same way, and the scripts still run from the command line.

## Database and async

### 1. Database through models instead of hand-written SQL (2026-09-30)

**Files:** `backend/database.py`, `backend/models/orm.py` (new), `backend/routers/annotation.py`, `auth.py`, `kv_pairs.py`, `backend/scripts/data_creation.py`, `kv_generator.py`, `build_embeddings.py`

**Why it's best practice:** SQL written by hand in strings is easy to get wrong and open to injection. Model classes (SQLAlchemy) describe each table once, and the library builds the queries. A test fails if anyone adds raw SQL again.

**Benefit:** Fewer bugs, safer queries, and one place to read what each table looks like.

### 2. Async database access (2026-09-30)

**Files:** `backend/database.py`, `backend/routers/annotation.py`, `auth.py`, `kv_pairs.py`, `backend/startup.py`, `backend/scripts/kv_generator.py`

**Why it's best practice:** A web server answers many people at once. If a request waits on the database in the normal blocking way, everyone behind it waits too. With async (`aiosqlite`), the server does other work while the database answers. Each request gets its own session (`get_db_session`) that is always closed, even on errors.

**Benefit:** Requests no longer block each other on database calls. Command-line scripts keep a plain blocking helper (`get_sync_session`, `read_hadiths_df`), and `kv_generator.py` wraps the async calls with `asyncio.run`, so nothing about how you run scripts changed.

### 3. Async tests (2026-09-30)

**Files:** `tests/conftest.py`, `tests/test_routers.py`, `test_search_api.py`, `test_app_factory.py`, `test_startup.py`, `test_services.py`, `pyproject.toml`

**Why it's best practice:** Async code has to be tested as async. 32 tests use `async def` and call the real app through `httpx.AsyncClient`, with no server started and no network. pytest-asyncio runs in auto mode, so no decorator is needed on each test.

**Benefit:** The tests exercise the same path a browser request takes, including the database session handling.

## Tests

### 4. Test setup, speed and naming (2026-09-30)

**Files:** `tests/` (22 test files), `tests/conftest.py`, `pyproject.toml`, `requirements-dev.txt`

**Why it's best practice:** Tests need no real data, internet or GPU. `conftest.py` builds a 3-hadith corpus, a small search index, fake embeddings, and a fake E5 model. Every test gets its own temporary database file, so `pytest-xdist` can run them in parallel (`-n auto`) without clashing. Fixtures and helpers start with `_` so it is clear they are not called directly. The virtual environment is named `.venv` and uses Python 3.12, the same as the Dockerfile.

**Benefit:** The full suite takes under half a minute and runs the same on any machine. Run it with `.venv/bin/python -m pytest`.

### 5. Snapshot tests (2026-09-30)

**Files:** `tests/_snapshot.py`, `tests/test_snapshots.py`, `tests/snapshots/` (29 saved files)

**Why it's best practice:** Before the big cleanup, the statistics and table output of the evaluation scripts were saved to files. Tests compare fresh output against them. This is the standard safety net for restructuring code that has no tests yet.

**Benefit:** We could split the long evaluation scripts into small functions and prove the numbers did not change. If a number changes on purpose, run the tests with `UPDATE_SNAPSHOTS=1` to save the new version.

### 6. Checks that the tests are actually good (2026-09-30)

**Files:** `tools/mutation.sh`, `tools/mutation.pyproject.toml`, `tests/test_mutation_killers.py`; vulture settings in `pyproject.toml`

**Why it's best practice:** Line coverage only says code ran. Mutation testing (mutmut) makes small deliberate breaks in the code and checks that some test fails. Vulture lists code nobody calls.

**Benefit:** We found weak spots and wrote tests for them. Vulture reports no dead code now.

## Code structure

### 7. Settings in one place, and an app factory (2026-09-30)

**Files:** `backend/features.py` (new), `backend/main.py`, `.env.example` (new)

**Why it's best practice:** `main.py` used to read environment variables and build the app at import time. Now `create_app()` builds it and takes its settings as arguments, so tests can build an app with any combination of switches. Switches for optional parts (annotation, key-value pairs, benchmark, search, dense retrieval, fine-tuned adapter) live in `features.py`. `APP_MODE` still works as a preset, and any `FEATURE_<NAME>` variable overrides it.

**Benefit:** You can run a light version (annotation only) or the full search stack without editing code. `.env.example` lists every variable without holding real keys.

### 8. Retrieval methods as a list (2026-09-30)

**Files:** `backend/services/` (new: `retrieval.py`, `results.py`, `agreement.py`), `backend/routers/search.py` (284 lines down to 37)

**Why it's best practice:** Each search method is one entry in a registry that says which switches it needs. The router builds one `POST /search/<name>` route per enabled entry. What a route needs (indices, model) is passed in (`SearchContext`, FastAPI `Depends`) and not imported as a global. The old functions in `scripts/search.py` were kept and the new code wraps them, so old callers keep working while code moves over step by step.

**Benefit:** Adding a search method means adding one entry, not editing a big function. Tests can swap in fake indices and a fake model.

### 9. Long scripts split into short functions (2026-09-30)

**Files:** `backend/scripts/preprocess.py`, `full_evaluation.py`, `finetune.py`, `finetune_eval.py`, `evaluation.py`, `profile.py`, `stats_tests.py`, `data_creation.py`, `build_embeddings.py`, `llm_validation.py`, `build_all.py`, and the new `eval_pipeline.py`

**Why it's best practice:** A function that does one thing is easy to read and to test. Ruff now fails any function with more than 10 branches. The steps shared by `evaluation.py` and `finetune_eval.py` moved into `eval_pipeline.py`, so they are written once.

**Benefit:** Bugs are easier to find, and there is less copy-pasted code.

### 10. Shorter import paths (2026-09-30)

**Files:** `backend/models/__init__.py`, `backend/routers/__init__.py`, `backend/scripts/__init__.py`, `backend/lazy_exports.py`

**Why it's best practice:** Code imports from the package (`from models import Hadith`) and does not need to know which file holds the class.

**Benefit:** Files can be moved or split later without breaking every import. Heavy modules still load only when used, so annotation mode never loads the search stack.

## Tooling and safety

### 11. Style checks and pre-commit hooks (2026-09-30)

**Files:** `pyproject.toml`, `.pre-commit-config.yaml`, `.gitignore`, `requirements-dev.txt`

**Why it's best practice:** Ruff and black decide spacing and import order, so reviews only show real changes. Hooks run before every commit and stop mistakes at the earliest point: gitleaks looks for passwords and API keys, and the other hooks run ruff, black, vulture and Hadolint, plus checks for large files and merge leftovers. `.gitignore` had two entries joined on one line (`*.pyc` and `mutants/`), which we fixed.

**Benefit:** A leaked key never reaches the repository history, where it would be hard to remove. Run `.venv/bin/pre-commit install` once after cloning.

### 12. Smaller, safer Docker image (2026-09-30)

**Files:** `Dockerfile`, `.dockerignore`

**Why it's best practice:** The Dockerfile has three stages, so compilers and build tools stay out of the final image. The app runs as a normal user instead of root, so a break-in gets less. Downloaded packages are cached between builds, and slow steps come first so small code edits rebuild fast. `.dockerignore` was excluding `docker-entrypoint.sh`, which the Dockerfile needs, and was not skipping `.venv` or the tests.

**Benefit:** Faster rebuilds, a smaller attack surface, and the CAMeL Arabic data now really downloads at build time (the old step always failed quietly).

### 13. Security scans and library upgrades (2026-09-30)

**Files:** `tools/scan-image.sh`, `.github/workflows/docker-scan.yml`, `requirements.txt`

**Why it's best practice:** Trivy checks the image for known security holes and dockle checks it against container good-practice rules. The workflow runs both on every push, every pull request and every Monday, because new holes are found without any code change.

**Benefit:** The first scan found 46 fixable problems. After the library upgrades (see "Read this first") there are none. Run it locally with `docker build --pull -t hadith-search:hardened . && tools/scan-image.sh`.

### 14. Dockerfile lint, health check, labels (2026-09-30)

**Files:** `Dockerfile`, `.hadolint.yaml`

**Why it's best practice:** Hadolint reads the Dockerfile for mistakes. A health check lets Docker see when the app has stopped answering. Labels record what the image is and which commit built it.

**Benefit:** Docker can restart a hung container by itself. Anyone holding an image can tell which commit it came from.

### 15. Notes for the next person (2026-09-30)

**Files:** `CLAUDE.md`, `docs/WIKI.md` (key rename), this file

**Why it's best practice:** Setup steps and rules written down in the repo stay with the code.

**Benefit:** A new developer, or an AI assistant, can start work without asking what the commands are.

## REST API

### 16. Resource URLs under /api/v1 (2026-09-30)

**Files:** `backend/rest.py` (new), `backend/tokens.py` (new), `backend/routers/` (`auth.py`, `annotation.py`, `kv_pairs.py`, `benchmark.py`, `search.py` rewritten; `hadiths.py` and `root.py` new), `backend/main.py`, `frontend/src/api/`, `frontend/src/pages/`, `tests/`

**Why it's best practice:** A REST API names things (annotators, assignments, searches), not actions, and uses the HTTP verb for the action. Reads use GET so browsers and proxies can cache them. Answers use the standard status codes: 201 for created (with a `Location` header), 401 without a valid token, 403 for someone else's data, 404 for missing, 409 for a duplicate username, 422 for bad input. Error bodies use the standard `application/problem+json` shape. Each response carries `_links` to related URLs, and `GET /api/v1` lists the entry points, so a client can follow links instead of hard-coding paths.

**Benefit:** Repeat searches and benchmark reads come from the browser cache or get a 304 (the `ETag` is a hash of the body). Timing moved from the body into the `Server-Timing` header, because a changing number in the body would break that. Nothing is kept in server memory between requests, so more servers can be added behind a load balancer.

| Old | New |
| --- | --- |
| `POST /search/{method}` (JSON body) | `GET /api/v1/searches?q=&method=&lang=&grade_filter=&book_filter=` |
| `GET /hadith/{id}` | `GET /api/v1/hadiths/{id}` |
| `POST /auth/signup`, `/auth/signin` | `POST /api/v1/annotators`, `POST /api/v1/tokens` |
| `GET /auth/me`, `POST /auth/signout` | `GET /api/v1/annotators/me` (no sign-out call: the client drops the token) |
| `GET /annotation/queries`, `/annotation/{id}/current` | `GET /api/v1/assignments`, `/api/v1/assignments/{id}` |
| `POST /annotation/{id}/label` | `PUT /api/v1/assignments/{id}/labels/{hadith_id}` with `{"label": 0-2}` |
| `POST /annotation/{id}/navigate?index=` | `PUT /api/v1/assignments/{id}/progress` with `{"index": n}` |
| `/kv-pairs/stats`, `POST /kv-pairs/{id}/verify`, `/kv-pairs/export` | `/api/v1/kv-pairs/statistics`, `PATCH /api/v1/kv-pairs/{id}`, `?status=verified` |
| `/benchmark/*` | `/api/v1/benchmark/*` |

Behavior that differs from before:

- Saving a label no longer moves the cursor. The frontend calls the progress endpoint after it.
- A query that is not assigned to you is now 404, not 403.
- A missing benchmark file is now 404, not a 200 with `{"error": ...}`.
- The `mode` parameter of `/benchmark/finetuned` accepts only letters, digits and underscores. It used to build a file path from raw input.
- The `response_time_ms` field is gone from search results; read the `Server-Timing` header.

### 17. Signed tokens instead of a sessions table (2026-09-30)

**Files:** `backend/tokens.py`, `backend/routers/auth.py`, `backend/models/orm.py`, `backend/database.py`, `.env.example`

**Why it's best practice:** The old code stored each login in a database table and looked it up on every request. A signed token (JWT) carries the annotator id and an expiry, and any server that knows `AUTH_SECRET` can check it. That is what makes it possible to run more than one server.

**Benefit:** No database read per request for auth. The cost: a token cannot be cancelled before it expires (12 hours by default, `AUTH_TOKEN_TTL_MINUTES`), and if `AUTH_SECRET` is unset the server makes a random one, so logins break on restart. The old `auth_sessions` table is no longer created; existing rows are ignored.

**Still open:** the `/kv-pairs` routes have no login check, as before. Running several servers also needs a database they all share; the SQLite file is per machine.

## Security review

### 18. Fixes from a security pass over the app (2026-09-30)

**Files:** `backend/main.py`, `backend/routers/auth.py`, `backend/routers/kv_pairs.py`, `backend/routers/search.py`, `frontend/src/pages/KvVerificationPage.tsx`, `.github/workflows/docker-scan.yml`, `requirements.txt`, `tests/test_app_factory.py`, `tests/test_routers.py`

**Why it's best practice:** Code written fast (by a person or an assistant) tends to skip the checks nobody sees in a demo. I went through the usual list (who can call what, how passwords and tokens are made, what user input reaches, what leaves the server, what CI trusts) and fixed what was really there:

- **Anyone could read files off the server.** The route that serves the frontend joined the URL onto the folder path, so a URL with an encoded `../` returned files outside it. I reproduced it before fixing. It now resolves the real path and refuses anything outside the frontend folder.
- **The `/kv-pairs` routes needed no login**, including the ones that change data. All of them now need a token, and the frontend page sends it.
- **Password hashing was too cheap.** It used 100,000 rounds; the current guidance for this method is 600,000. New passwords use 600,000 and store the count with the salt. Old rows still verify with 100,000, so nobody is locked out.
- **Sign-in gave away which usernames exist**, because an unknown name skipped the hash and answered faster. It now does the same work either way.
- **Input had no size limits.** Passwords are capped at 128 characters (minimum raised from 6 to 8), usernames at 64, search text at 500. Otherwise one huge request could tie up the hash or the search.
- **CORS was wide open.** It allowed every method and header, with credentials. Tokens travel in a header, not a cookie, so credentials are now off and only the needed methods and headers are allowed.
- **No browser protection headers.** Every response now carries `Content-Security-Policy` (own scripts, Google Fonts, nothing else), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, a referrer policy and a permissions policy. The `/docs` page is left without the CSP because it loads scripts from a CDN.
- **CI actions were pinned to moving tags.** They are now pinned to commit hashes, so a hijacked tag cannot change what CI runs.
- **Dependencies:** `requests`, `urllib3`, `idna`, `pygments` and `soupsieve` were bumped to the versions that fix reported holes.

**Benefit:** Nine tests cover these (traversal, headers, login required, limits, same answer for unknown user and wrong password, old hashes still verify). All 363 tests pass.

**Checked and fine:** no SQL built from strings (SQLAlchemy only), no `eval`/`exec`/shell calls with user input, no `dangerouslySetInnerHTML`, no committed secrets (gitleaks runs on every commit), the only outgoing HTTP calls go to fixed URLs (Jina and the LLM API), tokens pin HS256 and require an expiry, and there are no webhooks.

**Still open, on purpose:**

- **The sign-in limit is not a hard lockout.** It is 5 tries at once then 1 a minute (item 19), and it only exists when nginx is in front. Several nginx instances would each count on their own.
- **The token lives in `localStorage`**, so an XSS bug would expose it. The CSP and the absence of raw HTML rendering reduce that risk; an httpOnly cookie would remove it but needs CSRF handling.
- **Tokens cannot be revoked** before they expire (12 hours by default).
- **Some dependency reports are not fixed:** `torch` (2.11 to 2.13), `datasets` (4 to 5), `setuptools`, `accelerate`, `nltk`, and React Router (needs version 7). The Python ones are build and training tools, or need a wider retest. React Router's open redirect needs a `<Link>` or `navigate()` fed a user-controlled URL, which this app does not do. Bump them when you can retest.
- **Pickle loading is gone** (item 20). Only `migrate_to_postgres.py` touches the old files, and it reads `.npy` with `allow_pickle=False` and never opens the `.pkl` ones.
- **The LLM scripts** (`llm_grader.py`, `kv_generator.py`) send hadith text to a model. It is offline tooling and its output is a label, so prompt injection has little to hit, but treat its output as untrusted.

### 19. nginx in front of the app, with a sign-in rate limit (2026-09-30)

**Files:** `nginx/default.conf` (new), `nginx/proxy_app.conf` (new), `docker-compose.yml`, `tools/test-nginx.sh` (new), `.github/workflows/docker-scan.yml`, `backend/routers/auth.py`, `backend/main.py`, `requirements.txt`

**Why it's best practice:** Rate limiting belongs at the edge. nginx turns away a flood before it reaches Python, it counts in one place even with several app servers (no shared store needed), and it sees the real client address. It also caps request bodies at 1 MB, hides its version and follows the app container by name when it restarts. The nginx container is the `nginx-unprivileged` image, so like the app it does not run as root.

**Benefit:** Sign-in and sign-up allow 5 tries at once per client address, then 1 more per minute (429 after that, as `application/problem+json` with `Retry-After: 60`). Every other route is not limited. Guessing passwords is slowed hard. The site is served on port 8000 through nginx: the app's own port is not published. Start it with `tools/deploy.sh init` (item 21); plain `docker compose up` no longer starts an app.

**What changed and why:**

- An earlier version used SlowAPI inside the app (per-address 10 tries per 20 minutes, 120 a minute for everything else). It was removed, together with its four packages. The app now has no rate limiting of its own, so running uvicorn without nginx has none.
- nginx cannot do "10 tries, then locked for 20 minutes". Its slowest refill is 1 per minute, so the rule is 5 tries then 1 a minute (at most 65 in the first hour). The choice was made on purpose; an exact lockout would need an nginx script module or a counter in the app.
- General traffic (searches, reads) is not limited any more. The earlier 120 a minute is gone by choice.

**Test it:** `tools/test-nginx.sh` starts nginx with a stub app in Docker and checks the limit, the JSON 429, that other routes are not limited and the body cap. The scan workflow runs it too. It does not build the real app image, so run `docker compose up --build` once and try the site before relying on it.

**If another proxy sits in front of nginx** (Traefik, Dokploy, a cloud load balancer), nginx would see that proxy's address for everyone and all clients would share one counter. Then add nginx's `real_ip` settings (`set_real_ip_from <proxy address>; real_ip_header X-Forwarded-For;`) or move the limit into that proxy. Also note `docker-compose.yml` still has `CORS_ORIGINS=*`.

**Docker on Windows or WSL** can hide the real client address (all requests look like the Docker gateway). The limit is correct on a Linux server; do not judge it from a Windows laptop.

### 20. PostgreSQL with pgvector replaces SQLite, the pickles and the .npy files (2026-09-30)

**Files:** `backend/database.py`, `backend/models/orm.py`, `backend/services/ranking.py` (new), `backend/services/retrieval.py`, `backend/services/results.py`, `backend/scripts/build_inverted_index.py`, `backend/scripts/build_embeddings.py`, `backend/scripts/embedding_store.py` (new), `backend/scripts/migrate_to_postgres.py` (new), `backend/scripts/search.py` (cut down), `backend/scripts/loading.py` (cut down), `docker-compose.yml`, `.env.example`, `tests/conftest.py`, `tests/test_ranking.py` (new), `tests/_legacy_search.py` (new)

**Why it's best practice:** The embeddings and the BM25 index used to be files that were loaded whole into memory at startup, so every server held its own copy and any change meant rebuilding and reloading files. Now they live next to the hadiths they describe, and a search asks the database for what it needs. `.pkl` loading is gone too, which removes the "unpickling runs code" risk.

**Benefit:** Startup no longer loads an index. The database enforces links between tables again (hadith to embedding, posting and length rows, annotator to labels) and deletes their dependents with them. Several app servers can share one database. The tests run against a real Postgres, one schema per test.

**What changed and why:**

- **Everything is in PostgreSQL** (decided with you): hadiths, embeddings, BM25 postings, annotators, assignments, labels and KV pairs. SQLite is gone. `DATABASE_URL` is required (`postgresql+psycopg://...`).
- **BM25 and TF-IDF run in SQL** over `postings`, `terms` and `hadith_lengths`, with the same formula as before (k1 1.2, b 0.75, idf `ln((N-df+0.5)/(df+0.5))`). `tests/test_ranking.py` runs the old in-memory code (kept as `tests/_legacy_search.py`) on the same data and checks the scores match to 9 decimals, for English and Arabic queries, all lexical systems and the pseudo relevance feedback ones.
- **Dense search is exact** (no vector index), float32, cosine distance from pgvector. At this corpus size an index would add recall loss for no speed you would notice; add an HNSW index later if the corpus grows by an order of magnitude.
- **Ties are now broken by hadith id.** Scores that agree to 9 decimals count as tied and the lower id comes first, so the same query always returns the same order (the old code's tie order depended on dict order).
- **The BM25 + TF-IDF hybrid now returns its results sorted by score.** The old function returned them in set order, so the `bm25-tf-idf` endpoint listed hadiths in an arbitrary order. The scores are unchanged.
- **The eval pool restriction is a `WHERE hadith_id IN (...)`** inside the query (`restrict=`), not a masked array.

**Moving an old install:** start Postgres, set `DATABASE_URL`, then run `python scripts/migrate_to_postgres.py` from `backend/`. It copies `hadiths.db`, loads the `.npy` embeddings and rebuilds the BM25 index from the preprocessed text columns. It does not read the `.pkl` files. It refuses to run if the hadiths table already has rows. `finetune_eval.py` still overwrites the stored embeddings with the fine-tuned ones, as it overwrote the `.npy` files before; run `build_embeddings.py` again to get the base vectors back.

**Test it:** the tests need a Postgres with pgvector. Without one they skip with a message that shows the `docker run` line. Set `TEST_DATABASE_URL` to use your own. There is no CI job that runs the tests yet; add one with a `pgvector/pgvector:pg17` service.

**Not checked here:** I did not build the app image or run `docker compose up` with the real data, and the local NLTK data is missing so I could not run a real English query end to end (the ASGI tests use a stub preprocessor). The Docker image was not re-scanned.

### 21. Blue/green releases and canaries on one host (2026-09-30)

**Files:** `tools/deploy.sh` (new), `docker-compose.yml`, `nginx/default.conf`, `nginx/proxy_app.conf`, `tools/test-nginx.sh`, `tests/test_deploy_script.py` (new), `tests/test_database.py`, `.gitignore`, `.env.example`

**Why it's best practice:** Two copies of the app (blue and green) run side by side behind nginx. A new version starts on the idle colour, is checked, and then takes traffic in steps. Going back is a config reload, not a rebuild, so a bad release costs seconds, and users never see a restart.

**Benefit:** `tools/deploy.sh` gives you `init`, `status`, `deploy`, `canary N`, `promote`, `rollback` and `stop-idle`. Traffic only moves to a colour whose container reports healthy, a failed deploy is stopped and leaves live traffic alone, and a canary that turns unhealthy while watched puts all traffic back on the live colour by itself.

**Decisions made with you:**

- **Platform:** one host, docker compose plus the nginx already in front of the app. No Kubernetes.
- **Canary split:** by a hash of the client address (`split_clients` on `$remote_addr`), at a percentage you choose. One address stays on one colour for the whole canary, so a person does not bounce between versions.
- **Database:** one shared PostgreSQL for both colours. Schema changes must be backward compatible (see the rule below).
- **Promotion:** manual commands with an automatic health gate. There is no automatic promotion on error rate, because the app does not export metrics yet.

**How a release goes:**

```bash
tools/deploy.sh init                # first time only: postgres, nginx and blue
tools/deploy.sh deploy --build      # build the working tree, start it on the idle colour, wait for healthy
tools/deploy.sh canary 10           # 10% of client addresses go to the new colour; watched for 30 s
tools/deploy.sh canary 50           # raise it as you gain confidence
tools/deploy.sh promote             # everyone on the new colour; the old one keeps running
tools/deploy.sh rollback            # in a canary: stop it. After a promote: go back to the old colour
tools/deploy.sh stop-idle           # when you are sure, stop the old colour
```

`deploy IMAGE` uses an image you built or pulled instead of building. Responses carry an `X-Release: blue|green` header, so you can see which colour answered. `.env` needs `POSTGRES_PASSWORD` and `AUTH_SECRET` (compose refuses to start without them). `AUTH_SECRET` must be the same on both colours, or a token from one is rejected by the other.

**The schema rule:** both colours run against the same database at the same time, and the app only ever adds tables and columns (`create_all`, no migrations). So a release may add tables and columns, but must not rename or drop anything the previous release reads or writes. Do a rename in two releases: add the new name and write both, then remove the old one in the next release once the old colour is gone. `tests/test_database.py` checks that starting the older release does not remove columns or tables a newer one added. Embeddings are one Arabic column with no fixed size (the English column was dropped later, see items 28 and 33), so a release that changes the embedding model must re-encode every row before it goes live; a canary cannot mix two models in one column.

**How the routing works:** `tools/deploy.sh` writes `deploy/state/routing.conf` (git-ignored) and runs `nginx -s reload`. nginx checks the file first (`nginx -t`); if it rejects it, the old file is restored and nothing changes. The state (live colour, canary percent, previous colour) is in `deploy/state/state.env`. The app service in compose is now `app-blue` and `app-green`, each behind a profile, so plain `docker compose up` starts no app: use `tools/deploy.sh init`.

**Test it:** `tests/test_deploy_script.py` runs the script against a fake `docker` and checks every transition (canary, health gate, failed deploy, nginx rejecting the file, promote, rollback, stop-idle). `tools/test-nginx.sh` (also run in CI) checks the real nginx: live colour, promote, rollback, and that one address stays on one colour during a canary.

**Not checked here:** I did not run `deploy.sh` against real containers (no app image was built in this checkout), and I could not test the percentage split over many client addresses, because every local request comes from one address. The split itself is nginx's documented `split_clients`. Try `deploy --build`, `canary 10` and `promote` once on the real host before relying on it.

**Limits:**

- **One host.** If the machine goes down, both colours go with it. This is release safety, not high availability.
- **Memory:** both colours run at once during a release. With `APP_MODE=search` each loads its own Arabic encoder, so the host needs room for two.
- **Behind another proxy** (see item 19) every client may share one address, so a canary would send all or none of them to the new colour. Fix the real address first.
- **One colour is live at a time in the state file.** Do not run two `deploy.sh` commands at once.

### 22. The Jina reranker methods are removed (2026-09-30)

**Files:** `backend/scripts/search.py`, `backend/services/ranking.py`, `backend/services/retrieval.py`, `backend/features.py`, `backend/scripts/eval_pipeline.py`, `backend/scripts/pooling.py`, `backend/scripts/stats_tests.py`, `frontend/src/pages/UserSearchPage.tsx`, `frontend/src/types/index.ts`, tests and snapshots, `.env.example`, `docker-compose.yml`, `README.md`, `CLAUDE.md`, `docs/`

**What changed:** the two search methods that called an outside API are gone: `cross-encoder-rerank` and `final-pipeline` (both used the Jina `jina-reranker-v3` API). With them went the `FEATURE_CROSS_ENCODER` flag, `JINA_API_KEY`, the 30-second Jina rate limit, the "Advanced search" button on the user search page, and the `BM25_CROSS_ENCODER` and `FINAL_PIPELINE` systems in the evaluation and pooling code. The app now has 8 search methods, and none of them calls an outside service. The LLM scripts (`llm_grader.py`, `kv_generator.py`) are offline tools, not search methods, and were left alone.

**Effects you should know about:**

- **The user search page** always uses `bm25-prf`. The toggle that switched it to `final-pipeline` is removed.
- **Pooling** now takes the union of five systems instead of six, and `E5_DEPENDENT_SYSTEMS` lost `FINAL_PIPELINE`. If you already built `qrels_ungraded.json` with the old six, the pool and any human labels made on it still stand, but a fresh pooling run gives a pool without the Jina system's top results. Do not mix the two when comparing.
- **Earlier evaluation results** that include `BM25_CROSS_ENCODER` or `FINAL_PIPELINE` can no longer be reproduced by this code.
- **Snapshot tests:** the synthetic systems in `tests/test_snapshots.py` changed (its fifth system is now `BM25_ROCCHIO`) and the golden files were regenerated.
- **Docs:** `README.md`, `docs/WIKI.md`, `docs/ARCHITECTURE.md`, `docs/EVALUATION.md` and `docs/FINE_TUNING.md` no longer list the two methods. Older items in this note that mention Jina describe how things were then.

**Follow-up fix (search method picker):** the user search page had no method picker, and the dev page's picker listed methods the server had switched off, so choosing one showed a red error panel (HTTP 422). Both pages now use a shared `AlgorithmSelect` filled from `GET /api/v1/search-methods` (`frontend/src/api/useSearchMethods.ts`), so only methods the server offers are listed. The picker is there before the first search, changing it re-runs the search, and a method in the URL that the server does not offer falls back to `bm25-prf`. I checked both pages in headless Chromium against the running app: five options listed, no error panel, switching to BM25 changed the URL and results, and `?algorithm=final-pipeline` fell back to `bm25-prf`. `tsc` shows the same 6 errors as before, none in the files I touched.

### 23. Semantic search uses an Arabic ONNX model instead of multilingual E5 (2026-09-30 to 2026-10-01)

> **Superseded (2026-10-04, see item 33):** the model named below, `akhooli/sbert-nli-500k-triplets-MB` at 256 dimensions, was replaced by `masterofaudio2077/Fada_ar_embedding` at 64 dimensions. The text of this item is kept as the record of the first Arabic model. The recall numbers below were measured with the old model and 256 dimensions; they are not the numbers of the current model. The `english` column mentioned below was dropped in item 28.

**Files:** `backend/scripts/export_onnx.py` (new), `backend/scripts/arabic_encoder.py` (new), `backend/scripts/recall_proxy.py` (new), `backend/scripts/loading.py`, `backend/scripts/build_embeddings.py`, `backend/scripts/embedding_store.py`, `backend/services/ranking.py`, `backend/services/retrieval.py`, `backend/routers/search.py`, `backend/scripts/eval_pipeline.py`, `backend/scripts/pooling.py`, `backend/scripts/finetune_eval.py`, `backend/scripts/migrate_to_postgres.py`, `backend/features.py`, `frontend/src/api/useSearchMethods.ts`, `requirements.txt`, tests.

**What changed:** the dense methods (`cosine-similarity`, `semantic-rerank`, `semantic-rrf`) now use `akhooli/sbert-nli-500k-triplets-MB` (ModernBERT, Arabic only, trained with Matryoshka loss, Apache-2.0, revision `73ca7f3`). `scripts/export_onnx.py` downloads it and writes an ONNX file whose graph does the mean pooling, keeps the first 256 values and normalises them. Queries run through ONNX Runtime, so torch is only needed to export. Choices made with the owner: Arabic only, vectors cut to 256, newest opset that passes.

**Opset:** the script tries opsets from the newest ONNX knows (28) down to 17 and keeps the first that ONNX Runtime 1.30 loads and whose output matches the PyTorch model (cosine 0.9999 or better on five Arabic texts, single and padded batch). Opsets 28, 27 and 26 were rejected by ONNX Runtime ("invalid graph" in the attention node). **Opset 25 was accepted, lowest cosine 0.999977.** The result is in `export.json` next to the model.

**Effects you should know about:**

- **English has no semantic search.** The dense methods return HTTP 422 for `lang=en`; `/search-methods` lists `languages` for each method and the picker shows only methods that fit the chosen language.
- **Evaluation:** dense systems are scored on Arabic queries only (`ARABIC_ONLY_SYSTEMS` in `pooling.py`), and pooling skips them for English queries. Their numbers are not comparable with the BM25 rows, which cover all queries, and earlier results made with E5 cannot be reproduced.
- **The model is not in git** (596 MB). Create it with `cd backend && python -m scripts.export_onnx`; it goes to `backend/data/onnx/arabic` (in Docker, the `hadith-data` volume), or to `ARABIC_MODEL_DIR`. Then run `python -m scripts.build_embeddings` (about an hour on 16 CPU cores for 33,064 hadiths). Both steps ran inside the container on this machine.
- **The old E5 vectors are not used.** The `english` column of `hadith_embeddings` stayed (blue and green share one schema) but was empty; it is dropped in item 28. `migrate_to_postgres.py` no longer copies `.npy` embeddings.
- **Fine-tuning is disconnected.** `finetune.py` trains E5 LoRA adapters; `finetune_eval.py` stops with a message because those vectors would not match the new model. `FINETUNED_ADAPTER_PATH` is no longer read.
- **Dependencies:** `onnxruntime` is needed at run time; `onnx` and `onnxscript` only for the export.

**Recall@k of the new model (measured, proxy tests only):** there are no human relevance judgments on this machine (`queries.json` and `qrels_graded.json` are missing), so the project's own evaluation could not run. With your go-ahead I used two automatic tests, `backend/scripts/recall_proxy.py`, seed 42, Arabic queries through the app's real search systems. The numbers are in `docs/recall_proxy.json`. To rerun: `cd backend && python -m scripts.recall_proxy` (about 10 minutes).

*Test 1, known item (1000 queries).* The query is the first half of a hadith's Arabic text; relevant = that hadith and any copy that starts the same way. It rewards close wording, so it shows whether the model finds the hadith, not whether it understands a question.

| Method | recall@3 | recall@8 |
|---|---|---|
| cosine-similarity | 0.709 | 0.748 |
| semantic-rerank | 0.725 | 0.756 |
| semantic-rrf | 0.750 | 0.793 |

*Test 2, chapter title (238 queries, every distinct Arabic chapter title).* The query is a title such as "كتاب الصيام"; relevant = every hadith under that title in any book. Those sets hold 40 to 627 hadiths, so plain recall@3 could be 0.005 at best. The score is capped recall: hits in the top k divided by min(k, set size).

| Method | capped recall@3 | capped recall@8 |
|---|---|---|
| cosine-similarity | 0.064 | 0.057 |
| semantic-rerank | 0.122 | 0.113 |
| semantic-rrf | 0.123 | 0.113 |

**How to read it:**

- **Semantic RRF has the highest recall in test 1**, and cosine the lowest. The gaps are a few points and I did not test whether they are more than chance, so I do not claim a winner. In test 2, rerank and RRF are equal and about twice cosine.
- **Some known-item queries are missed even at k=8**: 1 - 0.793 = 21% of the recall is missing for RRF and 25% for cosine. I did not investigate why. A likely cause is that half a hadith is a poor query for a 256-dimension vector of the whole text, but that is a guess.
- **Test 2 is low, and is not a fair verdict on the model.** A chapter is a whole book (for example Fasting) and the title names a subject, while most hadiths in it never use the title's words.
- **Not measured:** the five keyword methods (you asked for the new model only), English (the model has no English), real user questions, and recall against human-graded qrels. Treat these numbers as a smoke test of the pipeline, not a comparison with E5. Replace them with real qrels when `queries.json` is available.
- **The script was simplified after the full run** (intervals and hit rates removed). I did not rerun all 1000 known-item queries. A 20-query run of the new script reproduced the chapter numbers above exactly, and `docs/recall_proxy.json` was reduced by hand to the recall values of the full run.

**Not checked:** whether this model finds better hadiths than E5 on the 20 evaluation queries (`queries.json` is missing on this machine). The 256-dimension cut was not compared with the full 768 dimensions.

### 24. Load testing and performance (2026-10-01)

All numbers: one app container capped at 2 vCPU and 4 GB (`APP_CPUS`, `APP_MEMORY` in compose), Locust on the same WSL2 host, 20 simulated users, 90 s, a mix of 9 endpoints (keyword, semantic, English and Arabic, weights in `tools/loadtest/locustfile.py`). Postgres is not capped. Load comes from the same machine, so real numbers will differ.

Reproduce: `tools/loadtest/run.sh LABEL USERS SECONDS [CONTAINER]` (prints p50/p95/p99, rps, container CPU and memory, OOM flag). Profile: `python -m scripts.serve_for_profiling` under Scalene (it shows mostly the idle main thread, since uvicorn work runs in threads; py-spy `--gil` and per-thread CPU were more useful).

Changes, in the order made (each re-measured):

| Change | rps | p95 | Notes |
|---|---|---|---|
| Baseline | 3.0 | 12 s | max memory 3.1 GB |
| Stop loading only the needed columns wasted: `build_results` cuts to `top_k` before loading, fetches only the columns it returns | 4.2 | 7.1 s | memory 1.5 GB |
| Search response serialised once (`EncodedJson`), byte-identical output | 3.7 | 7.7 s | no measurable gain, kept because it is simpler per request |
| ONNX Runtime given 1 thread, no spinning (`ARABIC_ENCODER_THREADS`, default 1) | 14.4 | 0.36 s | ORT sized its pool from the 16 host cores and spun inside a 2 CPU quota; one query encode went from 300-1000 ms to 27 ms |

- Before those: a cold start with 10 users killed the container (OOM at 4 GB) because every request thread loaded the CAMeL model and the encoder at once. `scripts/loading.py` now loads each once (`_load_once`, tested in `tests/test_loading.py`).
- At 60 users the same container gives 25 rps, p95 1.8 s, p99 2.3 s, no failures, memory 2.0 GB. It is CPU bound (Postgres is idle). No pool-exhaustion errors.
- Batch jobs are unaffected: `build_embeddings` and `export_onnx` pass `threads=0` (all cores).
- `synthetic`: users pick from 32 fixed queries; the 20-user run is one sample, not repeated.

**Not done:** two uvicorn workers (about 1.5 GB each), a bigger DB pool, startup warm-up of the models, limiting the 500-result response or gzip (this changes the API, your call), and the stale warnings printed by the entrypoint.

### 25. Settings class and dev/prod state (2026-10-03)

**Files:** `backend/settings.py` (new), `backend/main.py`, `backend/tokens.py`, `backend/database.py`, `backend/scripts/arabic_encoder.py`, `tests/test_settings.py` (new), `tests/conftest.py`, `requirements.txt` (adds `pydantic-settings==2.15.0`), `Dockerfile`, `.env.example`, `CLAUDE.md`.

**What changed:** the server's environment variables (`DATABASE_URL`, `AUTH_SECRET`, `AUTH_TOKEN_TTL_MINUTES`, `CORS_ORIGINS`, `STATIC_DIR`, `ARABIC_MODEL_DIR`, `ARABIC_ENCODER_THREADS`) are read by one pydantic `Settings` class instead of `os.environ` calls in four files. `get_settings()` is cached, so the environment is read once per process. `create_app` takes an optional `settings` argument. Feature flags (`APP_MODE`, `FEATURE_*`) and the script-only variables (`LLM_*`, `LORA_*`, `FINETUNE_*`) were left as they were, as agreed.

**`APP_ENV`:** `dev` (default), `test`, `prod`. In `prod` the app will not start unless `AUTH_SECRET` (32 or more characters) and `CORS_ORIGINS` are set, and it serves no `/docs`, `/redoc` or `/openapi.json`. `dev` and `test` behave as before: a random secret with a warning, and local Vite origins for CORS. `*` is still accepted for `CORS_ORIGINS` in prod because it counts as set; say if prod should refuse it.

**Things to know:**
- Nothing sets `APP_ENV=prod` yet. Compose and the Dockerfile run as `dev` until you add it to `.env`.
- The Docker `HEALTHCHECK` now calls `/api/v1` instead of `/openapi.json`, because prod hides the schema. The image was not rebuilt.
- `AUTH_SECRET` shorter than 32 characters now raises a pydantic `ValidationError` (a `ValueError`) at startup, not a `RuntimeError` on first use.
- Tests that change an environment variable must call `get_settings.cache_clear()`. `tests/conftest.py` clears it around every test and stops tests from reading the repo `.env`.
- Existing coupling, not new: `tests/test_app_factory.py::test_lifespan_initialises_db_and_preloads` fails if `frontend/dist` exists, because `static_dir=""` falls back to it.

**Database lifecycle (2026-10-03):** the app's lifespan now has three clear phases. On startup it creates the schema and, if the database cannot be reached, stops with "Cannot reach the database (...); check DATABASE_URL and that PostgreSQL is running" instead of a raw driver error. Then it serves. On shutdown, inside a `finally`, it calls `dispose_engines()` so pooled connections are closed even if startup or the app raised. Before this, shutdown only printed a message and nothing closed the pools except the test fixtures. Three tests in `tests/test_app_factory.py` cover it.

### 26. No status codes or technical error text in the UI (2026-10-03)

**Files:** `frontend/src/api/errors.ts` (new), `frontend/src/api/ApiContext.tsx`, `frontend/src/api/AuthContext.tsx`, `frontend/src/api/services.ts`, `frontend/src/components/ErrorBanner.tsx`, the sign-in, sign-up, annotation and KV pages, `frontend/src/i18n/translations/ar.ts`.

**What changed:** users no longer see things like "HTTP 422" or "Search failed: 500", and the English `title` and `detail` from the server's error responses are no longer shown either. One function, `errorKey()` in `api/errors.ts`, turns any failure into an Arabic message: no connection, invalid input (400, 422), session expired (401), not allowed (403), not found (404), conflict (409), too many attempts (429), and a generic "something went wrong" for everything else. The number is still kept on `ApiError.status` so code can react to a 401, but it is never printed. Error state now holds a translation key and `ErrorBanner` and the pages show `t(key)`. A wrong password on sign-in has its own message. The sign-up checks (short name, short password, mismatch) are now Arabic too. The same rule applies to the dev pages.

**Also fixed:** the old type errors (`isLanding`, `qrelData`, `idx`, the compare page passing a third argument to `search`). `npx tsc -b` and `vite build` pass. `npm run lint` still reports older problems that are not about errors: React fast-refresh rules in `ApiContext`, `AuthContext` and `Navbar`, and one setState-in-effect in `AuthContext`.

**Test isolation (2026-10-03):** `_pg_schema` in `tests/conftest.py` already gave every DB test its own schema. It now also tags each connection with the schema name (`application_name`) and, in teardown, ends any connection of that test that is still open, which rolls back its transaction and frees its locks, before `DROP SCHEMA`. Other tests running in parallel are not touched. Tests: `tests/test_db_isolation.py`.

### 27. Queue for search spikes (2026-10-04)

**Files:** `backend/limiter.py` (new), `backend/settings.py`, `backend/main.py`, `backend/routers/search.py`, `nginx/default.conf`, `tools/test-nginx.sh`, `tests/test_limiter.py` (new), `frontend/src/api/errors.ts`, `frontend/src/i18n/translations/ar.ts`, `.env.example`, `CLAUDE.md`.

**What changed:** only `GET /api/v1/searches` is queued, in two layers, as agreed. nginx smooths each client address: up to 10 searches a second go straight through, up to 20 more wait and are released at 10 a second, and more than 8 in flight from one address are refused. The app then runs at most 8 searches at once, holds up to 64 more in a first-in-first-out queue for at most 5 seconds, and answers 503 with `Retry-After: 5` beyond that. Both 503s are problem+json. The sizes are settings (`SEARCH_MAX_CONCURRENT`, `SEARCH_QUEUE_SIZE`, `SEARCH_QUEUE_TIMEOUT_SECONDS`). The frontend shows an Arabic "server busy, try again" message for 503. Sign-in keeps its existing hard 429.

**Measured (one run, 2 CPU / 4 GB rig, same Locust mix, 100 users, 90 s, no nginx):** 34.8 rps, p50 1.5 s, p95 1.9 s, p99 2.0 s, 2 of 3,102 requests refused with 503 "queue full (64 waiting)", no OOM. Before the queue the same load gave 34.5 rps, p95 2.5 s, p99 3.2 s and no refusals. Throughput is unchanged (the app is the limit); the queue caps how long a request can wait.

**Things to know:**
- The app queue is per process. Blue and green, or a second worker, each have their own queue, so the real total is the sum.
- `limit_conn` counts a delayed request as in flight, so one address sending a large burst is refused sooner than the burst size alone suggests.
- Sizes were not tuned beyond this one run. The nginx part was tested only against a stub app (`tools/test-nginx.sh`), not the real image, which was not rebuilt.
- The throwaway profiling container needed `pip install pydantic-settings` by hand because its image predates it.

### 28. Third normal form (2026-10-04)

**Files:** `backend/models/orm.py`, `backend/database.py`, `backend/scripts/data_creation.py`, `preprocess.py`, `build_all.py`, `kv_generator.py`, `migrate_to_postgres.py`, `recall_proxy.py`, `profile.py`, `backend/services/results.py`, `ranking.py`, `backend/routers/annotation.py`, `kv_pairs.py`, `tests/test_normalization.py` and the tests that built `hadiths` rows.

**What was wrong.** An audit of the tables found two places where a fact was stored more than once:

- `hadiths` repeated chapter and book facts on every row, held five columns that copied another column of the same row, and carried the six `Preprocessed_*` texts that are derived data.
- `kv_pairs.hadith_en` / `hadith_ar` copied hadith text that `hadiths` already has for `hadith_id`.

All other tables (`hadith_embeddings`, `hadith_lengths`, `terms`, `postings`, `annotators`, `assignments`, `annotations`, `annotation_progress`) were already in 3NF: one value per column, every non-key column depends on the whole key and on nothing else.

**What the schema is now**

| Change | Result |
|---|---|
| New `books` (`book` PK, `lk_book` unique) | `hadiths.LK_Book` is gone. `hadiths.Book` is a foreign key to it. |
| New `chapters` (PK `book`, `chapter_number`; `title_english`, `title_arabic`) | `Chapter_Title_English/Arabic` and the duplicate `Chapter_English/Arabic` are gone from `hadiths`. `(Book, Chapter_Number)` is a composite foreign key to it. |
| New `hadith_preprocessed` (`hadith_id` PK and FK, six `Preprocessed_*` columns) | The six columns are gone from `hadiths`. `preprocess.py` writes the table, BM25 build and PRF read it. |
| Same-row duplicates removed | `English_Hadith`, `Arabic_Hadith` (kept `English_Text`, `Arabic_Text`) and `Grade` (kept `English_Grade`). The `*_Source` columns stay: they say where a text came from, they are not copies. |
| `kv_pairs` | `hadith_en` / `hadith_ar` dropped; `hadith_id` is a foreign key to `hadiths.id`. `GET /api/v1/kv-pairs` joins `hadiths` and returns the same fields as before. |
| `annotations.hadith_id` | Foreign key to `hadiths.id` (`ON DELETE CASCADE`, like `kv_pairs`). |

API output is unchanged. Search results still return `raw_grade` and the chapter titles; `GET /api/v1/hadiths/{id}` still returns the old names (`Grade`, `English_Hadith`, `Arabic_Hadith`, `Chapter_English`, `Chapter_Arabic`) computed from the stored columns, so the frontend needed no change. `database.read_hadiths_df()` with no columns returns a flat frame (hadiths joined with chapter, book and preprocessed texts, ordered by id); `database.insert_hadith_rows()` is the one place that splits a flat row into the tables, used by the loader, the SQLite migration and the tests.

**Decision: sections are not a table.** The check queries on the real database (33,064 rows, run read-only on 2026-10-04):

| Question | Result |
|---|---|
| Is the chapter title fixed by (Book, Chapter_Number)? | Yes: 0 conflicting pairs |
| Is the section title fixed by (Book, Section_Number)? | **No: 735 conflicting pairs** |
| Does a chapter sit in exactly one section? | **No: 276 chapters span more than one section** |
| Rows with no `Chapter_Number` / no `Section_Number` / no `Book` | 0 / 6,161 / 0 |
| `English_Text` vs `English_Hadith`, `Arabic_Text` vs `Arabic_Hadith`, `Chapter_English` vs `Chapter_Title_English`, `Chapter_Arabic` vs `Chapter_Title_Arabic`, `Grade` vs `English_Grade` | 0 rows differ in every pair |
| Distinct `LK_Book` per `Book` | 1 for each of the 6 books |

Because (Book, Section_Number) is not a key, a `sections` table would have to guess what a section is, and a `DISTINCT` copy would merge or lose titles. So `Section_Number`, `Section_English` and `Section_Arabic` stay on `hadiths`. Splitting them properly needs the owner to say what identifies a section in the LK corpus (probably the section number is only unique inside a chapter, or the numbering was re-used by the source). Until then `hadiths` is in 3NF for the chapter and book facts, and the section columns are a known, documented exception.

**Behaviour changes to know about**
- A KV pair now shows the English and the Arabic text of the same hadith (`hadith_id`). Before, `hadith_ar` came from a separate Arabic search and could be a different hadith than `hadith_id`.
- Rebuilding the corpus (`data_creation.create_database`) drops the tables with `CASCADE` and puts the foreign keys from `annotations` and `kv_pairs` back at the end of the same transaction. If a stored annotation or KV pair points at an id the new corpus does not have, the rebuild fails and the old corpus stays.
- `insert_hadith_rows` refuses two different titles for one chapter instead of keeping one.
- `scripts/migrate_to_postgres.py` splits an old flat SQLite `hadiths` table (also one that still has `Chapter_English`, `Grade` and the other old names).

**Migrating the shared database (owner runs it; not run on the real data).** Both colours share one database, so it is two steps. Take a backup first (`pg_dump`). Both scripts were run on a scratch copy with 3 hadiths and then used by the new code (search results, KV join, BM25 feedback query), then the copy was dropped. Check the numbers on the real data before step 2.

Step 1 is additive: the old release keeps working, because no column is removed. Run it, then deploy the new release. Do not rebuild the corpus with the old release after step 1 (its `DROP TABLE` has no `CASCADE` and would fail on the new foreign keys).

Before step 1, check that no stored id is orphaned (both must return 0):

```sql
SELECT count(*) FROM annotations a LEFT JOIN hadiths h ON h.id = a.hadith_id WHERE h.id IS NULL;
SELECT count(*) FROM kv_pairs k LEFT JOIN hadiths h ON h.id = k.hadith_id WHERE h.id IS NULL;
```

```sql
BEGIN;
CREATE TABLE books (book TEXT PRIMARY KEY, lk_book TEXT UNIQUE);
INSERT INTO books SELECT DISTINCT "Book", "LK_Book" FROM hadiths WHERE "Book" IS NOT NULL;

CREATE TABLE chapters (
  book TEXT NOT NULL REFERENCES books(book),
  chapter_number INTEGER NOT NULL,
  title_english TEXT,
  title_arabic TEXT,
  PRIMARY KEY (book, chapter_number));
INSERT INTO chapters
  SELECT DISTINCT "Book", "Chapter_Number", "Chapter_Title_English", "Chapter_Title_Arabic"
  FROM hadiths WHERE "Book" IS NOT NULL AND "Chapter_Number" IS NOT NULL;

CREATE TABLE hadith_preprocessed (
  hadith_id INTEGER PRIMARY KEY REFERENCES hadiths(id) ON DELETE CASCADE,
  "Preprocessed_English" TEXT, "Preprocessed_Arabic" TEXT,
  "Preprocessed_English_Isnad" TEXT, "Preprocessed_Arabic_Isnad" TEXT,
  "Preprocessed_English_Matn" TEXT, "Preprocessed_Arabic_Matn" TEXT);
INSERT INTO hadith_preprocessed
  SELECT id, "Preprocessed_English", "Preprocessed_Arabic", "Preprocessed_English_Isnad",
         "Preprocessed_Arabic_Isnad", "Preprocessed_English_Matn", "Preprocessed_Arabic_Matn"
  FROM hadiths;

ALTER TABLE hadiths ADD CONSTRAINT hadiths_Book_fkey FOREIGN KEY ("Book") REFERENCES books(book);
ALTER TABLE hadiths ADD CONSTRAINT fk_hadiths_chapter
  FOREIGN KEY ("Book", "Chapter_Number") REFERENCES chapters(book, chapter_number);
ALTER TABLE annotations ADD CONSTRAINT annotations_hadith_id_fkey
  FOREIGN KEY (hadith_id) REFERENCES hadiths(id) ON DELETE CASCADE;
ALTER TABLE kv_pairs ADD CONSTRAINT kv_pairs_hadith_id_fkey
  FOREIGN KEY (hadith_id) REFERENCES hadiths(id) ON DELETE CASCADE;
COMMIT;
```

Step 2 drops the old columns. Run it only after the old release is gone. It stops with an error if any duplicate column differs from the one that stays.

```sql
BEGIN;
-- Stop with an error if any copy differs from the column that stays (the check found 0 rows).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM hadiths
             WHERE "English_Text" IS DISTINCT FROM "English_Hadith"
                OR "Arabic_Text" IS DISTINCT FROM "Arabic_Hadith"
                OR "English_Grade" IS DISTINCT FROM "Grade"
                OR "Chapter_Title_English" IS DISTINCT FROM "Chapter_English"
                OR "Chapter_Title_Arabic" IS DISTINCT FROM "Chapter_Arabic") THEN
    RAISE EXCEPTION 'a duplicate column differs from the column that stays; not dropping';
  END IF;
END $$;
ALTER TABLE hadiths
  DROP COLUMN "LK_Book", DROP COLUMN "Chapter_Title_English", DROP COLUMN "Chapter_Title_Arabic",
  DROP COLUMN "Chapter_English", DROP COLUMN "Chapter_Arabic",
  DROP COLUMN "English_Hadith", DROP COLUMN "Arabic_Hadith", DROP COLUMN "Grade",
  DROP COLUMN "Preprocessed_English", DROP COLUMN "Preprocessed_Arabic",
  DROP COLUMN "Preprocessed_English_Isnad", DROP COLUMN "Preprocessed_Arabic_Isnad",
  DROP COLUMN "Preprocessed_English_Matn", DROP COLUMN "Preprocessed_Arabic_Matn";
ALTER TABLE kv_pairs DROP COLUMN hadith_en, DROP COLUMN hadith_ar;
COMMIT;
```

**Step 2b: drop the unused English vector column (destructive, run after the `pg_dump` and only once the old release is gone).** `hadith_embeddings.english` was never filled after the move to the Arabic model (item 23), and no code reads it (dense search is Arabic only). Checked read-only on the real database on 2026-10-04: 33,064 rows, 0 of them with an `english` value, and the table has only its primary key index, so nothing is lost and no index depends on the column. The old release's ORM still lists the column, so drop it only after that release is gone (the schema rule in item 21). Not run on the real database.

```sql
-- must return 0 before you drop anything
SELECT count(*) FROM hadith_embeddings WHERE english IS NOT NULL;
ALTER TABLE hadith_embeddings DROP COLUMN english;
```

**Tests:** `tests/test_normalization.py` (keys, foreign keys, removed columns, `hadith_embeddings` holds only the Arabic vector, sections stay on `hadiths`, rebuild keeps the references), plus the existing suites adjusted to the new tables. `ruff`, `black` and `vulture` are clean.

**Still open (owner)**
1. What identifies a section? Answer that and a `sections` table can be added the same way as `chapters`.
2. `hadith_embeddings`, `hadith_lengths`, `terms.df` and `Normalized_Grade` are derived data kept on purpose (rebuilt by the pipeline, used by queries). Say so if you want any of them recomputed on read instead.

### 29. Image scan failure: sentence-transformers bump (2026-10-04)

**Files:** `requirements.txt`.

**What happened:** the `docker-scan` CI job failed in trivy on one finding: `sentence-transformers` 5.4.1, CVE-2026-68770 (CRITICAL, remote code execution through a security control bypass), fixed in 5.6.0. Nothing else, including the Debian base, had findings. The library is only used by `backend/scripts/export_onnx.py` (reference vectors) and fine-tuning, not at query time.

**Fix:** pin raised from `sentence-transformers==5.4.1` to `==5.6.0`, the lowest release that has the fix. Its dependencies already fit the existing pins: `uv pip check` reports the environment compatible, and no other pin changed. In the repo `.venv`, `import sentence_transformers` gives 5.6.0, `tests/test_arabic_encoder.py` passes (7 tests) and `from scripts.export_onnx import reference_vectors` imports. No Python code was changed.

**Not verified:** the image was not rebuilt and trivy/dockle were not rerun locally. Two local `docker build` attempts hung at the same step (`camel_data -i disambig-mle-calima-msa-r13`, a download that stalled partway), which is before the changed pip step matters, so the next CI run is the real check. The actual ONNX export with 5.6.0 was not run (it needs a model download).

### 30. SonarQube scan (2026-10-04)

**Files:** `sonar-project.properties` (new). `coverage.xml` is a generated file at the repo root and is not git-ignored, so do not commit it.

**How to repeat it:** start a throwaway server (`docker run -d --name hadith-sonar --memory 3g -p 9100:9000 sonarqube:community`), wait for `/api/system/status` to say UP, change the default admin password and create an analysis token through the API, then run `docker run --rm --network host -e SONAR_HOST_URL=http://localhost:9100 -e SONAR_TOKEN=... -v "$PWD:/usr/src" sonarsource/sonar-scanner-cli`. For coverage, run pytest with `--cov=backend --cov-report=xml:coverage.xml` (test database up, `frontend/dist` moved aside), then prefix every `filename="` in `coverage.xml` with `backend/` so Sonar can match the paths. The container and its volumes were removed afterwards, and the token was kept outside the repo.

**Result:** quality gate passed, but the default gate only looks at new code and this was the first analysis, so it says little. About 10,300 lines of code. 9 bugs, 4 vulnerabilities, 198 code smells, 0 security hotspots. Coverage 56.7%, duplication 2.9%. Ratings: reliability C, security C, maintainability A. By severity: 13 critical, 178 major, 20 minor, none blocker.

**Real findings worth fixing:**
- Accessibility in the frontend: `Footer.tsx:10-13` links with no real href, `HadithCard.tsx:22` and `HadithModal.tsx:46,50` click handlers on non-interactive elements with no keyboard support, labels not tied to inputs in `SigninPage.tsx:54,68` and `SignupPage.tsx:68,82,96`.
- Complexity: `DevAnnotationSessionPage.tsx:40` (24, limit 15), `scripts/stats_tests.py:44` (21), `api/validators.ts:48` (16). Nested ternaries in `DevAnnotationSessionPage.tsx:316,331,346`, `DevComparePage.tsx:205,226`, `KvVerificationPage.tsx:190,214`, `annotation.py:147`.
- `AuthContext.tsx:139` and `LanguageProvider.tsx:27` build a new context value on every render, which re-renders all consumers.
- FastAPI: 14 `HTTPException`s not listed in `responses=` (S8415, affects the OpenAPI docs only), `Annotated` dependencies in `benchmark.py:73,82` and `kv_pairs.py:55,56`, needless `async` in `rest.py:57,64` and `auth.py:98`.
- Duplicated string literals in `models/orm.py:112,185` and `services/__init__.py:8` (a constant would do).
- Unused parameters: `llm_grader.py:113`, `migrate_to_postgres.py:87`, `stats_tests.py:280`.

**Look like false positives or not worth acting on:**
- The 4 "vulnerabilities" are all `random` / `Math.random` (S2245) in `finetune.py:243`, `recall_proxy.py:47`, `HadithOfTheDay.tsx:18`, `UserHomePage.tsx:23`. None is used for security.
- 4 bugs in `scripts/check_evaluation.py:7-10` (float equality): it is a throwaway assert script with exact, hand-computed values.
- `export_onnx.py:194` (`sys.exit(main())` when `main` returns nothing): exits 0, which is intended.
- `validators.ts:34` (`forEach(validateSearchResult)`): the validator takes one argument, so the extra index is harmless.
- `DevAnnotationPage.tsx:56` (`await signout()`): harmless unless `signout` is truly synchronous.
- About 150 test-only smells (S9073 composite asserts, S5778 `pytest.raises` blocks with several calls, S3415 argument order): style, no bug.
- Script naming (`MAP`, `DocId`) matches metric names on purpose.

**Needs a decision:** whether to tune the Sonar rules (for example turn off S9073 for tests) and whether to add a Sonar job to CI. Coverage is only as high as the scripts under `backend/scripts` are tested.

### 31. Requirements traceability matrix (2026-10-04)

**Files:** `docs/TRACEABILITY.md` (new). No code or test changed.

**What it is:** 45 requirements taken only from the README, `CLAUDE.md`, the architecture, corpus and handoff docs, each linked to the code that implements it and to the tests that cover it (every test name was checked against `tests/`). It lists the requirements with no automated test (the Arabic error messages in the UI, image and secret scanning, the frontend method picker, the load-test figures; nginx limits only through `tools/test-nginx.sh`), the 56 tests that map to no requirement, and 8 open questions for the owner (the main one: no doc states ACID as a requirement, so most of `tests/test_acid.py` is unmapped).

**Not done:** no test or coverage run, so "covered" means a named test exists for the behaviour, not line coverage.

### 32. Schema and component diagrams, behaviour files (2026-10-04)

**Files:** `docs/diagrams/schema.dbml`, `schema.svg`, `components.mmd`, `components.excalidraw`, `components.svg`; `docs/behaviours/search.feature`, `spike-queue.feature`, `auth.feature`, `annotation.feature`, `kv-pairs.feature`, `friendly-errors.feature`, `prod-mode.feature`; `tools/diagrams/render.sh`, `entry.js`, `run.cjs`; two new sections in `README.md` ("Database design", "Components and behaviours").

**What changed:** the 13-table schema is described in DBML (tables, types, keys, foreign keys, indexes, notes on the derived tables and on the `Section_*` columns that stay on `hadiths`). The components and the calls between them are a Mermaid flowchart that is converted to an Excalidraw scene and exported to SVG. The `.feature` files are Gherkin written from the code and tests.

**Regenerate:** `tools/diagrams/render.sh schema` (checks the DBML with `dbml2sql --postgres`, draws the SVG with `@softwaretechnik/dbml-renderer`), and `CHROME=/path/to/chromium tools/diagrams/render.sh components` (bundles `@excalidraw/mermaid-to-excalidraw` and `@excalidraw/excalidraw` in a temp dir, runs them in headless Chromium, writes `components.excalidraw` and `components.svg`). Everything goes through npx or a temp dir; `package.json` is untouched. The DBML is written by hand from `backend/models/orm.py`, so change both together.

**Behaviour tests (pytest-bdd):** `pytest tests/bdd` runs the seven `.feature` files (`pytest-bdd==9.0.0` in `requirements-dev.txt`; steps in `tests/bdd/test_*.py`, shared helpers in `tests/bdd/conftest.py`). Result: 81 passed, 5 skipped on purpose (2 tagged `@nginx`, checked by `tools/test-nginx.sh`; 3 tagged `@manual`, which need a browser). pytest-bdd has no async steps, so each step is a plain function and the `_api` fixture runs requests on a private event loop. `tests/bdd` has an `__init__.py` so its `conftest.py` does not shadow `tests/conftest.py` (`from conftest import TEST_SECRET` in older tests would break otherwise). The `.feature` files are the only copy; wording changes go there. The friendly-errors scenarios bundle the real `frontend/src/api/errors.ts` with the frontend's esbuild and run it in node (they skip without `frontend/node_modules`). Wording fixes made while wiring the steps: kv-pairs filter steps split into When/Then; "Sign in" and "Wrong password" scenarios got the missing `Given "alice" is already signed up`; "Only POST is refused" became "POST is refused"; the `@nginx` and `@manual` tags were added. vulture ignores `@given`, `@when` and `@then` and the `pytest_collection_modifyitems` hook (see `pyproject.toml`).

**Things to know:** the layout of both SVGs is automatic and the bottom row of the components picture is crowded; edit `components.excalidraw` in Excalidraw if you want it tidier. `tools/test-nginx.sh` is the only check of the nginx scenarios in `spike-queue.feature` and `auth.feature`.

### 33. Embedding model is Fada at 64 dimensions, English vector column removed (2026-10-04)

**Current state (what to believe over item 23):**
- Model: `masterofaudio2077/Fada_ar_embedding` (ARBERTv2, mean pooling, Matryoshka), revision `7c0556b53fe8e0b323c53345594c689cf21657e0`, exported to ONNX at opset 25 with the first 64 of its 768 values kept and normalised (`EMBEDDING_DIM = 64` in `backend/scripts/arabic_encoder.py`). The export is checked against the PyTorch model (lowest parity cosine 0.99999988, in `export.json` next to the model) at `max_seq_length` 512.
- The real database holds 33,064 Arabic vectors of 64 dimensions, one per hadith (checked 2026-10-04).
- The first Arabic model (item 23, `akhooli/sbert-nli-500k-triplets-MB`, 256 dimensions) is history. Its recall numbers in item 23 and `docs/recall_proxy.json` are not valid for the current model; rerun `python -m scripts.recall_proxy` to get new ones.
- `hadith_embeddings.english` is removed from the ORM, the tests, the schema diagram and the docs. Nothing read it. The one-time `ALTER TABLE` for the shared database is step 2b of item 28 (not run).

**Docs corrected in this item:** `README.md`, `CLAUDE.md` and `docs/WIKI.md` said 256 dimensions; `docs/ARCHITECTURE.md` still named `scripts/search.py`, a pickle index and a SQLite-era `CREATE TABLE`, and said 256 dimensions; the corpus size was given as 33,491 in one place and about 33,064 in others. The loaded `hadiths` table has 33,064 rows; 33,491 is the size after the first-stage drop (`docs/CORPUS_DECISIONS.md`). `docs/FINE_TUNING.md` and the E5 parts of `docs/EVALUATION.md` still describe the old E5 work; both carry an "out of date" note at the top.

### 34. Coverage and mutation testing round (2026-10-04)

**Files:** `tests/test_mutation_killers_edges.py`, `test_mutation_killers_auth.py`, `test_mutation_killers_routers.py`, `test_mutation_killers_ranking.py`, `test_mutation_killers_recall.py` (new). No production code changed.

**Coverage (line and branch together, `pytest-cov`).** Last full run (whole tree, including the security and BDD suites and `promote_model.py`): 1186 passed, 9 skipped, 8 expected failures, 2 failed (`tests/test_normalization.py`, the two foreign-key tests: no `IntegrityError` raised; not part of this round). Total 74 % (4647 statements, 976 branches). The earlier run on the smaller tree was 71.6 % lines, 66.7 % branches. The routers, `database.py`, `services/` and the search code are at 93 to 100 %. What is low is script code that needs data, a model or the network: `kv_generator.py`, `llm_grader.py`, `export_onnx.py`, `finetune_eval.py`, `check_*.py` and `serve_for_profiling.py` (0 %), `pooling.py` (19 %), `profile.py` (36 %), `build_all.py` (36 %), `data_creation.py` (44 %).

**Mutation testing (mutmut 3.8.0).** Before this round, survivors were mostly exact values nothing asserted: JSON key names, error texts, a missing `outerjoin` condition, per-annotator and per-query scoping, tie order. The new tests pin these. Scope A (`limiter.py`, `settings.py`, `tokens.py`, `rest.py`, `startup.py`): 357 mutants, 339 killed, 5 timed out (counted as killed), 13 survivors, all equivalent: header names in a different case (HTTP and ASGI lower-case them), `flush=` on a `print`, `limit(1)` against `limit(2)` on a `== 0` check, `jwt.encode` without `algorithm="HS256"` (it is the default), `ensure_ascii=None` (falsy, same as `False`). `settings.py` and `tokens.py` have no real survivor.

Scope B (`routers/auth.py`, `routers/search.py`, `scripts/embedding_store.py`, `scripts/loading.py`, `search`): 297 mutants, 282 killed, 15 survivors before the last tests were added. Scope C (`database.py`, `services/`, `scripts/recall_proxy.py`, `routers/annotation.py`, `kv_pairs.py`, `hadiths.py`, `benchmark.py`): 1541 mutants, 1392 killed, 149 survivors reported by mutmut. Most of the 149 are false: a direct check (each mutant applied with `MUTANT_UNDER_TEST=<name> PYTHONPATH=. pytest -n0` in the staged `mutants/` folder) killed an earlier set of 167. The direct check of the final 149 was stopped at 111; 35 still survived, mostly in `services/ranking.py` (`bm25_tfidf_hybrid`, `_weights_table`, `_expansion_weights`: tie and weight details), `services/results.py` (`_in`, `build_results`) and `database.py` (`_flat_from`, engine options). These are not yet judged one by one; some are equivalent, the rest need a test.

**How to re-run.**

```bash
docker start hadith-test-pg
export TEST_DATABASE_URL=postgresql+psycopg://postgres:test-only-password@localhost:55432/hadith_test
mv frontend/dist /tmp/dist_aside   # only while running the whole suite; move it back after
.venv/bin/python -m pytest --cov=backend --cov-branch --cov-report=term-missing:skip-covered
tools/mutation.sh run --max-children 4
```

`tools/mutation.sh` only covers the modules listed in `tools/mutation.pyproject.toml`. To mutate others (`limiter.py`, `settings.py`, `tokens.py`, `rest.py`, `startup.py`, `database.py`, `services/`, `routers/`, `scripts/recall_proxy.py`) copy that file, change `source_paths`, and stage `backend/` and `tests/` the same way the script does.

**Things to know.**

- The staged copy has no `tools/`, so `tests/test_deploy_script.py` cannot run there; ignore it with `--ignore` in `pytest_add_cli_args`. Ignore `tests/bdd` too, and `tests/test_normalization.py` (one test fails only in the staged copy).
- With more than 2 workers and the database tests, mutmut reports many false survivors (a first run with 4 workers listed 623 survivors, 240 with one worker). Use `--max-children 1` or `2` for `database.py`, `services/` and `routers/`; 4 is fine for the pure modules in scope A.
- `mutmut` does not re-test existing results: delete the `mutants/` folder of the stage before a second run.
- mutmut cannot trace async generators well, so a few mutants in `limiter.search_slot` were first reported as survivors although a test fails with them applied.
- Known gap, left as is: `recall_proxy.run` divides by zero when the corpus has no hadith of at least 12 words.

### 35. Rollback for blue/green releases (2026-10-04)

**Files:** `tools/deploy.sh`, `tools/test-rollback.sh` (new), `backend/routers/root.py` (health route), `Dockerfile` (HEALTHCHECK), `tests/test_health.py`, `tests/bdd/test_health.py`, `docs/behaviours/health.feature`, `tests/test_deploy_script.py`, `README.md`, `CLAUDE.md`, `docs/TRACEABILITY.md` (R-46, R-47, R-48)

**Why it's best practice:** A release is only safe if going back is as easy as going forward, and if the old version is still there when you need it. Before this item `rollback` existed but nothing checked the new colour after the switch, the old version had no name, and a `deploy` after a `promote` silently made `rollback` point at the new, unpromoted colour.

**What you get:**

- **Release ids.** `deploy --build` tags the image with the git short sha (`hadith-search:<sha>`, or `<sha>-dirty-<time>` if the working tree has changes) and never overwrites a tag. `deploy IMAGE` uses the tag of `IMAGE` as the id. `deploy/state/releases.env` records the id and image of each colour and the last action, and `deploy/state/history.log` has one line per deploy, promote and rollback. `deploy`, `promote` and `rollback` never remove an image. Do not run `docker image prune -a`: the previous image must stay until the next successful release has been promoted. Old images are removed only by `prune-images` (below).
- **Automatic rollback in `promote`.** The new colour is smoke-tested before the switch (`GET /api/v1/health` and one search, asked directly from inside the nginx container; the exact rules are under "Health route and smoke check" below). Then traffic moves, and the script checks that nginx really serves the new colour, repeats the smoke test, and watches the container health for `PROMOTE_WATCH` seconds (default: the same as `CANARY_WATCH`, 30). If anything fails after the switch, traffic goes back to the old colour, nothing is stopped or removed, the script prints what failed and exits 1. If the failure is before the switch, nothing moves.
- **Manual `tools/deploy.sh rollback`.** Flips traffic to the previous colour: new routing file, `nginx -t`, then `nginx -s reload` (the existing `apply_routing`, so no request is dropped and a rejected config is put back). It refuses, and says why, if the previous colour is not running, not healthy or fails the smoke test. After a rollback, `rollback` again is refused (`promote` goes forward again; `rollback --force` flips anyway), so re-running it does not flip back and forth.
- **`--dry-run`** on `deploy`, `canary`, `promote`, `rollback`, `stop-idle` and `init` prints the plan and changes nothing (the read-only health checks still run).
- **`deploy` now clears the rollback target.** The idle colour is the rollback target after a promote, and `deploy` replaces it. The state's `PREVIOUS` is emptied then and the script prints the old image name, so you can start it again with `deploy IMAGE`.

**The database is NOT rolled back.** Step 1 of item 28 only adds things, so the previous release still runs on the new schema and rollback is safe. Step 2 and step 2b (dropping `hadith_embeddings.english` and the other old columns) cannot be undone by this feature, and the old release may then fail. The owner marks that by creating the file `deploy/state/schema-step2-applied` (or setting `SCHEMA_STEP2_APPLIED=1`). With the mark, `rollback` and the automatic rollback print a warning; they still do it, because a bad release on a working database is usually worse than a warning. The mark is optional and nothing sets it for you. If the old release cannot run after step 2, restore the `pg_dump` taken before step 2 (item 28), then roll back. Take that dump before every destructive step.

**Test it:** `tools/test-rollback.sh` (run by hand; it is not in CI, see below) runs a copy of `deploy.sh` against two stub apps and a real nginx on a private Docker network, with a small `docker` shim, and cleans up. It checks: the release id is recorded, promote, manual rollback and the refusal of a second one, dry runs change no file and no routing, rollback refused when the old colour is gone or unhealthy, promote refused when the smoke search fails, a busy (503) search only warns, a deployment without search skips the search check, promote refused when the health route fails, image pruning (dry run, newest 3 kept, images named in `releases.env` or used by a container kept, `--keep 0` refused), a deploy that never becomes healthy leaves traffic alone, automatic rollback when the new colour goes unhealthy after the switch, and the schema warning. About 2 minutes, because it waits for container health checks. It never touches `deploy/state` or the `hadith-*` containers. `tests/test_deploy_script.py` (fake docker, no containers needed, a few seconds) covers the same smoke and pruning rules, so they run with every `pytest`.

**Health route and smoke check (update, same day).**

- `GET /api/v1/health` is new (`backend/routers/root.py`). It answers `200 {"status":"ok"}` when the process is up and `SELECT 1` works on the database, and `503` as `application/problem+json` when the database is unreachable or takes longer than 3 seconds. It needs no token, does not use a search-queue slot, sends `Cache-Control: no-store` and says nothing about versions, settings or the database address. It exists in every `APP_MODE`. nginx has no cache and no rate limit on it (`location /` has none), so health checks are never throttled. The API root lists it as the `health` link.
- The Docker `HEALTHCHECK` and `tools/deploy.sh` (`SMOKE_HEALTH_PATH`, edge check, stub apps) now use `/api/v1/health`. Because it checks the database, a colour whose database is down reports unhealthy and no traffic moves to it; this is the intended readiness check. The image was not rebuilt, so the new `HEALTHCHECK` is untested in a real image.
- The smoke search no longer uses `bm25`. In `APP_MODE=annotation` there is no `/searches` route at all, and a search needs the loaded corpus, so a fixed `bm25` query could fail a healthy release. Now: the smoke check reads `GET /api/v1` and skips the search when `searches` is not listed. Otherwise it asks `GET /api/v1/searches?q=prayer&method=term-overlap&lang=en`. `term-overlap` has no feature requirement (it is on in every mode that has search), needs no model, and answers 200 with zero results on an empty corpus. 200 passes (zero results is fine); `503` (queue busy) is retried once after `SMOKE_RETRY_WAIT` (2 s) and then only warns; any other status, or no answer, fails. Overrides: `SMOKE_SEARCH=0` skips the search; `SMOKE_SEARCH_PATH=/api/v1/searches?...` uses your own request and must answer 200. The one case this cannot tell apart is a corpus that is loaded but broken in a way that still answers 200.
- **Image pruning.** `tools/deploy.sh prune-images [--keep N] [--dry-run]` removes old release images of the `hadith-search` repository (override the repository with `IMAGE_REPO`) and keeps the newest N (`KEEP_IMAGES`, default 3) by creation time. Only release tags count: `:blue`, `:green` and `:latest` are left alone. It never removes an image a running container uses or one that `deploy/state/releases.env` names (the live and the previous colour), uses plain `docker rmi` (no `-f`, never `docker image prune`), logs each removal to `history.log`, and does nothing when N or fewer release images exist. `--keep` must be 1 or more. Set `PRUNE_AFTER_PROMOTE=1` to run it after every successful promote (default off). Needs GNU `date` (Linux).
- **No CI step for the rollback test.** `tools/test-rollback.sh` was taken out of `.github/workflows/docker-scan.yml` because its automatic-rollback check is time-based and could fail on a slow runner. Run it by hand after editing `tools/deploy.sh`: `tools/test-rollback.sh` (about 2 minutes, needs Docker, builds no app image).

**Not checked:** the script was not run against the real app image or a real host, and production deploy is not finished, so no deploy workflow exists and nothing here assumes how production runs. The post-switch watch of 30 s makes `promote` take at least that long. A schema-step-2 mark still only warns on rollback; whether it should refuse unless `--force` is not decided.

### 36. Security test suite (2026-10-04)

**Files:** `tests/security/` (new: `__init__.py`, `conftest.py`, `_helpers.py`, `test_auth.py`, `test_accounts.py`, `test_injection.py`, `test_validation.py`, `test_error_hygiene.py`, `test_headers_cors.py`, `test_settings.py`, `test_resource_abuse.py`, `test_secrets.py`), `docs/TRACEABILITY.md` (R-47 to R-49), `CLAUDE.md`. The suite itself changed no production code; the fixes listed below did (`routers/auth.py`, `search.py`, `hadiths.py`, `kv_pairs.py`, `annotation.py`, `settings.py`, `docker-compose.yml`).

**What it does:** `pytest tests/security` drives the real `create_app` (not the cut-down `_client`) with httpx over ASGI, one schema per test. It checks bad tokens on every protected route (the routes are found by walking the app, and a second test lists the public ones so a new route has to be decided on), IDOR between two annotators, sign-up and sign-in limits and edge cases, SQL and markup payloads in every parameter, odd numbers and types, error bodies, prod hiding the docs, security headers and CORS, the nginx config text, Settings rules, the search queue under load, and a scan of the tracked files for keys (plus gitleaks when installed).

**Result of the last full run (private pgvector container, `frontend/dist` moved aside and restored):** whole suite 1103 passed, 9 skipped, 22 xfailed, before the fixes below. After them `tests/security` plus `tests/test_settings.py`: 486 passed, 8 xfailed.

**Fixed (2026-10-04, owner decisions):**
1. NUL characters. Usernames, `q`, `grade_filter`, `book_filter`, the kv-pairs `status` and `topic` filters and the `query_id` path now answer 422. The types live in `backend/inputs.py` (`Text`).
2. Ids. `GET /hadiths/{id}`, `PATCH /kv-pairs/{id}`, `PATCH /kv-pairs` and `PUT /assignments/{query_id}/labels/{hadith_id}` accept 0 to 2^31-1 (`DbId`); anything else is 422. `GET /annotators/{id}` was left alone: it only compares the id with the token, never queries with it.
3. `GET /kv-pairs?offset=` has a maximum of 1,000,000 (`MAX_OFFSET`; the table holds a few thousand pairs).
4. `PATCH /kv-pairs` takes at most 100 items (`MAX_BATCH`). The web page patches one pair at a time, so nothing in the frontend sends a batch.
6. `docker-compose.yml` now sets `APP_ENV=prod`, requires `CORS_ORIGINS` (`${CORS_ORIGINS:?...}`, no default) and still requires `AUTH_SECRET`. `Settings` refuses `CORS_ORIGINS=*` (also inside a list such as `https://a.example, *`) when `APP_ENV=prod`. Dev and test still accept `*`. Before `docker compose up`, put the real site origin in `.env`.

**Decisions, not bugs:**
- 5. No username normalisation. `Dave`, `dave` and `dave ` are separate accounts on purpose (`tests/security/test_accounts.py`).
- HSTS stays off until it is known what ends HTTPS (a reverse proxy or a platform such as Cloudflare). Set the header there, and then remove the xfail on `test_hsts_is_sent`.

**Still open, strict xfail (not asked to fix):** a 500 is plain text, not problem+json, and has no security headers; `AuthSettings` repr prints the signing secret; a too-short `AUTH_SECRET` is quoted in the start-up error; `database_url` shows in `model_dump_json`; token answers have no `Cache-Control: no-store`; no HSTS; sign-up answers 409 for a taken name, so names can be enumerated (accepted risk).

**Checked and fine:** no SQL built from strings (SQLAlchemy only), no `eval`/`exec`/shell calls with user input, no `dangerouslySetInnerHTML`, no committed secrets (gitleaks runs on every commit), the only outgoing HTTP calls go to fixed URLs (Jina and the LLM API), tokens pin HS256 and require an expiry, and there are no webhooks.

**Still open, on purpose:**

- **The sign-in limit is not a hard lockout.** It is 5 tries at once then 1 a minute (item 19), and it only exists when nginx is in front. Several nginx instances would each count on their own.
- **The token lives in `localStorage`**, so an XSS bug would expose it. The CSP and the absence of raw HTML rendering reduce that risk; an httpOnly cookie would remove it but needs CSRF handling.
- **Tokens cannot be revoked** before they expire (12 hours by default).
- **Some dependency reports are not fixed:** `torch` (2.11 to 2.13), `datasets` (4 to 5), `setuptools`, `accelerate`, `nltk`, and React Router (needs version 7). The Python ones are build and training tools, or need a wider retest. React Router's open redirect needs a `<Link>` or `navigate()` fed a user-controlled URL, which this app does not do. Bump them when you can retest.
- **Pickle loading is gone** (item 20). Only `migrate_to_postgres.py` touches the old files, and it reads `.npy` with `allow_pickle=False` and never opens the `.pkl` ones.
- **The LLM scripts** (`llm_grader.py`, `kv_generator.py`) send hadith text to a model. It is offline tooling and its output is a label, so prompt injection has little to hit, but treat its output as untrusted.

### 37. Performance run on the 3NF schema (2026-10-04)

**Files:** `tests/perf/test_search_perf.py` (new), `tools/loadtest/locustfile_methods.py` (new), `tools/loadtest/run.sh` (one line: `LOADTEST_FILE` picks another locustfile). No app code changed, no index added.

**Setup.** Same rig as item 24: a container capped at 2 CPU / 4 GB (`hadith-prof-new`, same image, the repo `backend` mounted read-only, the model volume mounted read-only), Locust on the same host. Old figures: the existing `hadith-prof` container, which still ran the code loaded before the 3NF change against the real, un-migrated `hadith-postgres`. New figures: `hadith-prof-new` against a scratch database `hadith_perf` inside `hadith-test-pg`, built from a `pg_dump` of the real database (read only) and migrated with the step 1, step 2 and step 2b SQL of item 28. That is the full corpus: 33,064 hadiths, 1,421,115 postings, 33,064 64-dimension vectors. The migration SQL ran without error on the full data, and `VACUUM ANALYZE` was run after. The scratch database and the new container were removed afterwards. Both databases are the same image (pgvector pg17) but two different servers, so a part of any difference may come from the server, not the schema.

**Commands.**

```bash
# the 3 steps: scratch DB, dump from the real DB, migrate (passwords are not printed)
docker exec hadith-test-pg psql -U postgres -c 'CREATE DATABASE hadith_perf'
docker exec hadith-test-pg psql -U postgres -d hadith_perf -c 'CREATE EXTENSION vector'
docker exec hadith-postgres pg_dump -U hadith -d hadith --no-owner --no-privileges \
  | docker exec -i hadith-test-pg psql -q -U postgres -d hadith_perf
# then item 28 step 1, step 2, step 2b (docker exec -i ... psql -v ON_ERROR_STOP=1 < file.sql)
LOADTEST_OUT=/tmp/perf tools/loadtest/run.sh new-mix20 20 90 CONTAINER          # mixed, as item 24
LOADTEST_FILE=tools/loadtest/locustfile_methods.py tools/loadtest/run.sh LABEL 20 90 CONTAINER  # 8 methods
RUN_PERF=1 PERF_BASE_URL=http://CONTAINER_IP:8000 .venv/bin/python -m pytest -n0 tests/perf -s
```

**Background load.** The coverage/mutation agent and the security-test agent were asked to pause. Four of my runs overlapped with their test runs (load average 2.5 to 4 on 16 cores, one with a `pytest-xdist` at 16 workers sharing `hadith-test-pg`); the first old 20-user run also had a 15 s stall in the first seconds (13 failed requests) and was dropped; those are left out of the tables below and only used to say that the 20-user numbers move by a factor of 2 in p95 when the host is busy. Every run in the tables started with a load average under 1.6 and no test or mutation process running (my driver script waited for that before each run; it is not in the repo). Run-to-run spread is not known beyond that: each row is one run, except 20 users, which I ran 4 times per side.

**Load test, mixed weights (item 24 mix), whole run**

| Users | Side | rps | p50 | p95 | p99 | Failed |
|---|---|---|---|---|---|---|
| 20 | old | 15.0 | 48 ms | 110 ms | 390 ms | 0 |
| 20 | new | 15.0 | 48 ms | 110 ms | 410 ms | 0 |
| 60 | old | 30.9 | 590 ms | 980 ms | 1.1 s | 0 |
| 60 | new | 32.6 | 470 ms | 930 ms | 1.1 s | 0 |
| 100 | old | 31.5 | 1.8 s | 2.3 s | 2.6 s | 31 of 2,806 (1.1%) 503 |
| 100 | new | 33.0 | 1.6 s | 2.1 s | 2.5 s | 8 of 2,942 (0.3%) 503 |
| 200 | old | 78 (incl. 503) | 70 ms | 2.6 s | 2.7 s | 4,404 of 7,773 (57%) 503 |
| 200 | new | 78 (incl. 503) | 88 ms | 2.6 s | 2.7 s | 4,370 of 7,762 (56%) 503 |

The 20-user rig is think-time bound (about 15 requests a second is what 20 users ask for), so it cannot show a difference in work per request. The container is CPU bound at 120% of 200% from about 60 users, plateau 31 to 33 successful requests a second at 100 users and 34 a second of successful responses at 200 (3,369 old and 3,392 new successful requests in 100 s). Memory 1.4 to 2.0 GB, no OOM, no restart.

**Per method, 20 users, each of the 13 method and language pairs at the same weight (`locustfile_methods.py`)**

| Method | Lang | old p50 / p95 / p99 (ms) | new p50 / p95 / p99 (ms) | Errors |
|---|---|---|---|---|
| term-overlap | en | 38 / 120 / 380 | 39 / 190 / 400 | 0 / 0 |
| term-overlap | ar | 42 / 340 / 690 | 45 / 82 / 660 | 0 / 0 |
| tfidf | en | 43 / 110 / 380 | 45 / 110 / 430 | 0 / 0 |
| tfidf | ar | 45 / 180 / 480 | 57 / 200 / 490 | 0 / 0 |
| bm25 | en | 37 / 61 / 230 | 40 / 280 / 430 | 0 / 0 |
| bm25 | ar | 43 / 190 / 1100 | 44 / 150 / 660 | 0 / 0 |
| bm25-tf-idf | en | 48 / 91 / 670 | 50 / 96 / 210 | 0 / 0 |
| bm25-tf-idf | ar | 58 / 110 / 410 | 60 / 110 / 350 | 0 / 0 |
| bm25-prf | en | 61 / 99 / 340 | 64 / 410 / 540 | 0 / 0 |
| bm25-prf | ar | 60 / 110 / 410 | 71 / 510 / 720 | 0 / 0 |
| cosine-similarity | ar | 35 / 56 / 300 | 33 / 86 / 230 | 0 / 0 |
| semantic-rerank | ar | 37 / 67 / 1100 | 39 / 97 / 290 | 0 / 0 |
| semantic-rrf | ar | 52 / 97 / 270 | 52 / 120 / 520 | 0 / 0 |
| All | | 45 / 110 / 420 (15.2 rps) | 49 / 180 / 520 (15.2 rps) | 0 / 0 |

Each row has 90 to 126 requests, so p95 and p99 rows are noisy (one slow request moves p99). This run overlapped lightly with another agent's tests (load average 1.1 to 2.6), and the bm25-prf rows in the new column look worse (p95 410 to 510 ms) than in the clean mixed runs (140 to 170 ms). Read it as "no method got slower in a way that holds up", not as a ranking. Median differences are 0 to 12 ms. The single-user test below is steadier.

**Single user, 20 requests per method (the new pytest)**

| Method | new p50 / p95 (ms) | old p50 / p95 (ms) |
|---|---|---|
| term-overlap en / ar | 28 / 38, 33 / 41 | 32 / 42, 34 / 44 |
| tfidf en / ar | 34 / 53, 38 / 55 | 39 / 56, 42 / 57 |
| bm25 en / ar | 32 / 45, 33 / 47 | 33 / 44, 37 / 52 |
| bm25-tf-idf en / ar | 40 / 66, 41 / 69 | 39 / 69, 43 / 69 |
| bm25-prf en / ar | 50 / 68, 47 / 71 | 44 / 66, 49 / 74 |
| cosine-similarity ar | 32 / 33 | 31 / 33 |
| semantic-rerank ar | 35 / 47 | 31 / 42 |
| semantic-rrf ar | 44 / 50 | 43 / 55 |

Old and new are the same within noise, which matches the plans below: the schema change adds a join of at most 500 rows to a 335-row table.

**Spike queue (150 or 300 requests at the same moment to one method, no nginx).** The app admits 8 running plus 64 waiting, so 72 get an answer and the rest are refused at once:

| Requests at once | Side | 200 | 503 | 200 p50 / max | 503 p50 |
|---|---|---|---|---|---|
| 100 | old / new | 72 / 72 | 28 / 28 | 1.7 s / 2.6 s, 1.2 s / 2.5 s | 0.39 s, 0.32 s |
| 150 | old / new | 72 / 72 | 78 / 78 | 2.0 s / 3.1 s, 1.2 s / 2.5 s | 0.37 s, 0.34 s |
| 300 | old / new | 72 / 78 | 228 / 222 | 1.9 s / 2.8 s, 1.8 s / 2.6 s | 0.80 s, 0.29 s |

Every 503 has `Retry-After: 5` and `application/problem+json` ("The server is busy. Try again in a moment."). No other status appeared and nobody waited longer than the 5 s timeout (longest answered request 3.1 s). In the sustained 100-user run the old side refused 1.1% and the new side 0.3% (queue full, 64 waiting, at the moment the users start together); at 200 users 56 to 57% of requests are refused and about 34 are answered each second, the same as the plateau. This is consistent with item 27.

**Plans (`EXPLAIN (ANALYZE, BUFFERS)` on the statements the app really sends, full corpus, new schema).** The statements were captured from the app (SQLAlchemy `before_cursor_execute`) for every method and language.

| Query | Plan | Time |
|---|---|---|
| Results (`hadiths` left join `chapters`, 500 ids) | index scan on `hadiths_pkey` for the ids, hash join with `chapters` (335 rows, 5 buffers) on `(Book, Chapter_Number)` | 1.2 ms for 500 rows, 0.2 ms for 20 |
| BM25 | `terms_pkey` and `postings_pkey` index scans, `hadith_lengths_pkey` lookup per posting, sort | 14 to 18 ms |
| TF-IDF | same without lengths | 12 to 15 ms |
| Term overlap | index only scan on `postings_pkey` | 5 ms |
| Corpus stats (count, average length) | sequential scan of `hadith_lengths`, 33,064 rows | 3 ms, run once per BM25 or TF-IDF call (twice in PRF and hybrid) |
| PRF feedback texts | `hadith_preprocessed_pkey`, 5 rows | 0.035 ms |
| Cosine (64 dimensions, no vector index) | sequential scan of `hadith_embeddings` with top-N heap sort | 8 to 9 ms |

The database time of a whole request is 20 to 40 ms; the rest of the 30 to 70 ms single-user time is query preprocessing, JSON and the encoder (about 27 ms, one thread). Inserted synthetic rows in the scratch copy (5,000 `kv_pairs`, 30,000 `annotations`, 5 annotators) to check the other new foreign keys:

| Query | Plan | Time |
|---|---|---|
| KV list (page of 50, and by topic) joined to `hadiths` | nested loop, `hadiths_pkey` lookup per row | 0.16 and 0.5 ms |
| Hadiths of one chapter, `WHERE "Book" = ... AND "Chapter_Number" = ...` | bitmap scan on `ix_hadiths_Book` (7,329 rows read, 46 kept) | 4.8 ms. No index on `(Book, Chapter_Number)`; no code path asks for it today |
| `kv_pairs WHERE hadith_id = ...` | sequential scan of 5,000 rows | 0.25 ms. No index on `kv_pairs.hadith_id` |
| `annotations WHERE hadith_id = ...` | sequential scan of 30,000 rows | 1.0 ms. No index; the primary key starts with `annotator_id` |
| `DELETE FROM hadiths WHERE id = ...` | six foreign-key triggers; the `annotations` one is the slowest | 2.5 ms in total |
| Annotations by annotator and query (the router) | `annotations_pkey` bitmap scan | 0.1 ms |

**Conclusions**
1. **No regression from the 3NF schema.** Single user is unchanged, 20 users is unchanged, at 60 and 100 users the new side is 3 to 6% faster in throughput with lower p50 (590 to 470 ms at 60 users). I do not claim the gain is the schema: the two sides use two Postgres servers, the new copy was loaded fresh (database 337 MB against 459 MB, `hadiths` 152 MB against 217 MB, `hadith_embeddings` 10 MB against 75 MB, so part of the difference is bloat that a `pg_dump` and restore removes), and each cell is a single run. What can be said is that narrower `hadiths` rows and the dropped columns do not hurt.
2. **The joins are cheap.** The chapter join is a hash join on 335 rows, and the PRF read of `hadith_preprocessed` is a primary-key lookup of 5 rows. KV reads use the primary key of `hadiths`.
3. **Missing indexes on the new foreign keys, reported and not added.** `hadiths("Book", "Chapter_Number")`, `kv_pairs.hadith_id` and `annotations.hadith_id` have none. The only queries that would use them are not in the app (no lookup by chapter or by hadith in kv/annotations) or are rare (deleting a hadith, a corpus rebuild, which drops the tables). Each of those scans costs 0.2 to 5 ms at today's sizes, so none is a regression. Add `kv_pairs(hadith_id)` and `annotations(hadith_id)` (additive `CREATE INDEX`) only when a feature deletes or looks up by hadith, or when annotations pass a few hundred thousand rows.
4. **The limit is the CPU of the container**, not Postgres: 120% of 200% busy with Postgres idle, 34 requests a second plateau from about 60 users, same as item 24 and item 27. The figures in the task (15.1 rps and p95 96 ms at 20 users) reproduce: 15.0 rps, p95 110 ms.
5. **Possible savings, not done** (your call): the corpus statistics (`count`, `avg` over `hadith_lengths`, 3 ms of every BM25 call, twice in PRF and hybrid) could be cached in the process and refreshed after an index rebuild; the 500-result response is large (see item 24). Neither was changed.
6. **The queue behaves as designed**: 8 + 64 admitted, the rest answered 503 with `Retry-After: 5` in about 0.3 s. With 100 users who arrive at once about 1% are refused.

**Repeat the test.** `tests/perf/test_search_perf.py` is skipped unless `RUN_PERF=1`, needs a running app (`PERF_BASE_URL`, default `http://127.0.0.1:8000`, the app container, not nginx) and `-n0`. It checks, with the limits in the table below: every method answers without error and inside the single-user p95 limit; 20 users for 20 s give the minimum rps and no errors; a 150-request burst gets only 200 or 503, every 503 has `Retry-After`, and not more than the allowed share is refused. It passed on both sides (single user 33 to 74 ms p95; 20 users 15.3 rps, p95 335 ms; 150 at once: 75 and 75). The limits are about 5 times the measured values so noise does not fail them, but a lost index or an ONNX encoder back on 16 threads would.

#### Performance budgets (read by tests/perf)

| Key | Value |
| --- | --- |
| `single_user_p95_ms` | 400 |
| `single_user_p95_ms:semantic-rerank` | 400 |
| `users20_min_rps` | 10 |
| `users20_max_p95_ms` | 1000 |
| `spike_max_refused_share` | 0.8 |

Keys with `:method` override the general key for that method. Change a number here and the test follows.

### 38. Frontend redesign "Warāqa" (2026-10-04)

Frontend only. No backend, database, nginx or API change, nothing committed.

- **What changed.** A new look (paper and ink, one red accent, folio-style results, a three-bar grade mark, full Arabic text with room for diacritics), light and dark, 360 px to wide screens. Colours and sizes were changed in `tailwind.config.js` under the same token names, so pages not rewritten changed with them. Rewritten: `Navbar`, `ThemeToggle`, `Footer`, `Layout`, `SearchBar`, the three filters (on a new `FilterSelect`), `HadithCard`, `HadithModal`, `HadithOfTheDay`, `Pagination`, `ErrorBanner`, `LoadingSpinner`, both home pages, both search pages and the annotation session page. New: `GradeSeal`, `FilterSelect`, `public/favicon.svg`. `constants.ts` now exports `GRADE_META` instead of `GRADE_COLORS`. About 40 new keys in `ar.ts` (only Arabic exists). `src/api/errors.ts` and the `error.*` keys are unchanged.
- **Accessibility.** axe-core on 13 pages, light and dark, 390 and 1440 px, against a stub API: 884 violating nodes before, 0 after. The fixes include labels on every select, a focus trap and focus return in the dialog (rendered through a portal, because an animated parent had stopped `fixed` from covering the page), tab-focusable scroll areas, `nav` and `aria-current` in pagination, and an h1 on the annotation page. A manual keyboard and screen-reader pass was not done.
- **Cards.** The hadith card is no longer a `role="button"` article (that was 240 of the axe nodes). The whole card still opens the details on click (unless text is selected), and keyboard users reach an "open details" button inside it. Check this if a test or habit relies on the card itself taking focus.
- **Method.** Mockups were HTML rendered with Chromium because no image generation was available; the built pages were compared with them. The taste skill is installed at project scope in `.claude/skills/` (with `skills-lock.json` at the repo root). Details, screenshots and the list of what was left out: `docs/design/README.md`.
- **Checks.** `npx tsc -b` passes, `npm run lint` is unchanged at 6 errors and 1 warning (all existed before; two are in `Navbar.tsx`, which keeps its old exports), `npm run build` passes and `frontend/dist` was rebuilt from this source. The known `test_lifespan_initialises_db_and_preloads` failure when `dist` exists is not related.
- **Not done.** The sign-in, sign-up, annotation list, guidelines, benchmark, compare and kv pages are restyled through the tokens but not redesigned, and their text is still partly English. No isnad display (no data). Only Chromium was tested.
- **Your call.** Whether to translate those remaining English pages; whether to keep the filters in a row (the mockup had a side column on wide screens).

### 39. Dead code check script (2026-10-04)

- **What.** `tools/dead-code.sh` wraps vulture. `check` (default) prints findings grouped by file and exits 1 if there are any; `report` always exits 0 and writes a Markdown report (per file, per type) to `$DEAD_CODE_OUT`, else `$CLAUDE_JOB_DIR/tmp/`, else the system temp dir; `whitelist` prints a `--make-whitelist` candidate and writes nothing. `--min-confidence N` overrides the config. It runs from the repo root, prefers `.venv/bin/vulture`, and never installs anything.
- **Config.** Paths, excludes, ignore lists and `min_confidence` stay in `[tool.vulture]` in `pyproject.toml`; the script has no copy of them.
- **Pre-commit.** The vulture hook now calls `tools/dead-code.sh check` (same files, same pass/fail; only the output is grouped).
- **Test.** `tools/test-dead-code.sh` builds a throwaway project with one dead and one used function and checks exit codes, report content and that `whitelist` writes no file (21 checks). Run it by hand after editing the script; it is not in CI.
- **Current result.** `tools/dead-code.sh check` is clean, also with `--min-confidence 0`. `tests/security/test_injection.py:225` is no longer flagged and needed no change.

### 40. Model promotion from MLflow (2026-10-05)

- **What.** `tools/promote_model.sh run` picks the best registered model version in MLflow, exports it to ONNX, re-embeds the corpus into a new table and stages it; `--promote` then starts it through `tools/deploy.sh` (deploy, health and search smoke check, promote, automatic switch back on failure). Code: `backend/scripts/promote_model.py`; extra install: `requirements-mlops.txt` (`mlflow-skinny`, never in the Dockerfile; mlflow is imported only when the script runs).
- **Settings.** Environment: `MLFLOW_TRACKING_URI`, `MLFLOW_TRACKING_TOKEN` (or any other MLflow auth variable; the script never prints or stores them), `DATABASE_URL`, `ARABIC_MODEL_DIR` (the live model, used as the baseline). Flags: `--registered-model` (or `MLFLOW_REGISTERED_MODEL`), `--margin` (0.01), `--pairs-artifact` (`eval/pairs.json`), `--pairs-run-id`, `--versions 3,4`, `--stage-dir`, `--dry-run`, `--promote` (wrapper only). A candidate is every READY version of the registered model except the one named in `deploy/state/model.env` (`MODEL_VERSION`). `--metric` from the first design is not used: the script never trusts a number logged by a run; it scores every model itself.
- **Metric.** The eval pairs file is a JSON list `[{"query": "...", "text": "...", "label": 2, "hadith_id": 17}]`, logged as an artifact of each version's source run. `label` is graded relevance (0 not relevant); `hadith_id` is optional. For each query the texts are ranked by cosine similarity of 64-d vectors (first 64 values, then unit length, the production path, with the same `encoding_text` cleanup), and the score is nDCG@10: DCG = sum of label / log2(rank + 1) over the top 10, divided by the DCG of the best possible order. The score is the mean over queries that have a positive label. Candidates are encoded with PyTorch (sentence-transformers); the baseline is the live ONNX model, so it is the number production really gets. If versions carry different pairs files (compared by sha256) the run stops; `--pairs-run-id` reads one file for all.
- **Gate.** The best version must beat the live model by `--margin` absolute (default 0.01). Then the ONNX export must reproduce the PyTorch vectors (`export_onnx`, minimum cosine 0.9999 over its test texts); a model that fails stops the run (exit 4) before any database write. Vectors stay 64-d. Exit codes: 0 done, 1 failed, 2 bad usage or settings, 3 no version beats the live model, 4 parity failed.
- **Steps.** select, export (to `<stage-dir>/mv<version>`), embed (all hadiths into `hadith_embeddings_mv<version>` plus a row in `embedding_sets`), stage (`deploy/state/model.staged.env`). `promotion.json` in the release folder records finished steps, so a re-run skips them (a half-embedded release is finished by upserting). Nothing the live colour uses changes in these steps.
- **Storage design.** Blue and green share one database, so the new vectors cannot overwrite `hadith_embeddings`: the live colour would then rank with new vectors and an old query encoder. Each release has its own table `hadith_embeddings_<release>` (same shape, `ON DELETE CASCADE` to `hadiths`) and a row in `embedding_sets` (release, model version, dim, created). The new colour is started with `EMBEDDINGS_RELEASE=<release>` and `ARABIC_MODEL_DIR=<release folder>`; unset means the old table, so existing installs do not change. At startup a colour with a release checks that the table has one vector per hadith and otherwise fails, so the health check keeps traffic off it. `drop_corpus_tables` (corpus rebuild) drops the release tables too. Both settings reach the container through `deploy/state/model.env`, which `deploy.sh deploy` sources (only the colour being created reads it; compose env is fixed at container creation).
- **Run it.** `tools/promote_model.sh run --registered-model NAME --dry-run` prints the pick, scores, margin decision and plan and writes nothing. `tools/promote_model.sh run --registered-model NAME` does the four steps. `tools/promote_model.sh run --registered-model NAME --promote` also copies the staged settings to `model.env` (old ones to `model.env.prev`), runs `deploy.sh deploy <live image>` and `deploy.sh promote`.
- **Rollback.** `promote` switches back by itself if the new colour fails its checks; the wrapper then restores `model.env.prev`. By hand: `tools/deploy.sh rollback`. The old colour keeps its old model and table, and the database keeps both embedding sets, so rollback needs no re-embedding.
- **Cleanup.** `tools/promote_model.sh prune [--keep 3] [--dry-run]` drops all but the newest 3 embedding sets (tables, registry rows, release folders). It never removes a release named in `model.env`, `model.env.prev` or `model.staged.env`. Like `deploy.sh prune-images`, run it by hand.
- **Tests.** `tests/mlops` (scoring with hand-computed nDCG, selection, margin pass and fail, missing pairs artifact, differing pairs, parity failure, dry run, resume, re-embed into a scratch database, default table untouched, prune, a real ONNX export of a tiny local BERT) uses a local file-based MLflow store; `tests/test_embedding_releases.py` covers the setting, search, startup check and rebuild; `tests/test_deploy_script.py` the `model.env` hand-over.
- **Not automated.** Creating or updating the eval pairs and logging them in MLflow; registering versions; the export assumes mean pooling and the Arabic wrapper in `export_onnx.py`, so a model with another pooling fails the parity check instead of being exported wrongly; the compose volume: `ARABIC_MODEL_DIR` is a path inside the container, so `--stage-dir` must be the host folder behind the `hadith-data` volume (`/app/backend/data/onnx/releases`), which the script does not check; no canary on quality (a canary by client address still works with `deploy.sh canary` instead of `promote`); no run against a real MLflow server or the real corpus has been done.
- **Open questions.** Tracking URI, registered model name, and the pairs artifact name and format (the `eval/pairs.json` default and the JSON format above are my choice). Whether 0.01 nDCG@10 is the right margin for your pairs set. Whether the stage folder should be the volume path. Whether old releases should be pruned after every promote (as `PRUNE_AFTER_PROMOTE` does for images).

### 41. CI on a self-hosted runner with GitHub-hosted fallback (2026-10-05)

**What changed:** `.github/workflows/docker-scan.yml` is replaced by `.github/workflows/ci.yml` with jobs `pick-runner`, `lint` (ruff, black, dead-code, actionlint), `frontend` (eslint ceiling + build), `test` (full pytest against a pgvector service container) and `docker-scan` (the old steps). Every job after the first uses `runs-on: ${{ fromJSON(needs.pick-runner.outputs.runner) }}`.

**Selector:** `tools/pick-runner.sh` asks the GitHub runners API (repository secret `RUNNERSTATUSTOKEN`, passed to the script as `RUNNER_STATUS_TOKEN`) for a runner labelled `hadith-ci` that is online and idle. Self-hosted is used only for push, schedule, workflow_dispatch and same-repository pull requests; fork pull requests always run on `ubuntu-latest` (the repo is public and the runner has a privileged Docker daemon). Any error, missing token or busy/offline runner falls back to `ubuntu-latest`; the reason goes to the job summary. Tested in `tests/test_pick_runner.py` with a fake API.

**Runner:** `tools/runner/` (compose with a pinned ephemeral `myoung34/github-runner` plus `docker:dind`, sharing one network namespace; its README has the steps). `tools/frontend-lint.sh` fails only above the known 6 errors / 1 warning. The docker build cache is `gha` on hosted runners and a local volume on the self-hosted one.

**Not done / not verified:** nothing ran on GitHub or on the self-hosted runner. No runner was registered, no secret was set, nothing was pushed. Known limits: the idle check and the job start are separate, so a runner that becomes busy in between makes the job queue instead of falling back; a runner switched off while jobs are queued for it leaves them waiting. `tools/test-rollback.sh` is intentionally not in CI. Whether the `test` job's `localhost:55432` service port is reachable through the shared namespace on the self-hosted runner is untested.

**Files:** `.github/workflows/ci.yml`, `tools/pick-runner.sh`, `tools/frontend-lint.sh`, `tools/runner/`, `tests/test_pick_runner.py`, `.gitignore`

### 42. Sized DB pool and optional micro-batching of the query encoder (2026-10-05)

**Files:** `backend/batching.py` (new), `backend/database.py`, `backend/settings.py`, `backend/scripts/loading.py`, `tests/test_batching.py` (new), `.env.example`.

**Connection reuse.** Both engines were already cached per URL with a pool and `pool_pre_ping`, but the pool was SQLAlchemy's default (5 connections, 10 extra) while the search queue lets 8 searches run. `database.pool_kwargs()` now sizes it from settings: `DB_POOL_SIZE` (default `SEARCH_MAX_CONCURRENT`, 8), `DB_POOL_OVERFLOW` (2) and `DB_POOL_RECYCLE_SECONDS` (1800). Tests that pass a `poolclass` (NullPool) keep it unchanged.

**Batching.** `MicroBatcher` wraps the encoder returned by `get_model()`: queries from concurrent searches that arrive within `ENCODER_BATCH_WAIT_MS` share one encoder call (up to `ENCODER_BATCH_MAX`, 16); calls with more texts than that, or with `batch_size`, go straight through. A failure in the encoder reaches every caller in the batch.

**It is off by default.** Measured on the exported model, pinned to 2 CPUs, 8 threads each encoding one query: 117.5 queries/s directly, 110.5 batched (p50 66 ms against 73 ms). A single caller went from 14 ms to 18 ms because of the 5 ms window. The encoder does about 117 queries/s while a whole search tops out near 26 per second (item 27 and the sweep below), so the encoder is not the limit; the CPU spent on SQL is. Set `ENCODER_BATCH_WAIT_MS=5` to turn it on, for example on a machine with more cores.

**Load sweep with the queue (rig `hadith-prof`, 2 CPU / 4 GB, no nginx, 2026-10-05, one run each):** 40 users 0 failures p99 1.1 s; 60 users 0 failures p99 1.8 s; 80 users 0 failures p99 2.3 s (about 26 rps); 100 users 4-6% refused, p99 3.2 s; 150 users 51% refused; 200 users 68%; 300 users 80%; 400 users 85%, p99 4.7 s. Every failure above 100 users was a 503 from the queue; no OOM, memory about 1.5 GB. The rig ran the code from before this item (queue present, default pool).

**Database layer, second part (2026-10-05), from an audit on synthetic data at 33K hadiths (HANDOFF figures are the real-corpus ones):**
- **Server-Timing now splits the time:** `search;dur=..., db;dur=..;desc="N statements", encode;dur=..` (`backend/timing.py`, SQLAlchemy cursor events). `encode` is absent for lexical methods and for repeated queries.
- **Corpus statistics are cached** for 5 minutes per database and language (`ranking._corpus_stats`; BM25, TF-IDF and PRF asked for them up to three times per search, 3.8 ms each). A rebuild by `build_all` runs in another process, so the time limit is what refreshes a running server; call `ranking.clear_corpus_stats()` in the same process.
- **Query vectors are cached** (1,024 entries, keyed by encoder object, cleaned text and `EMBEDDINGS_RELEASE`), so a repeated Arabic query skips the encoder (about 27 ms).
- **Three indexes added:** `annotations(hadith_id)`, `kv_pairs(hadith_id)`, `assignments(query_id)`. `create_all` skips existing tables, so `init_schema` and `init_schema_sync` now create missing indexes on those three small tables (`database.INDEXED_LATER`); additive, so an old release still runs.
- **N+1 removed:** `_labels_by_annotator` reads every annotator's labels in one query (two statements in total).
- **Default page is 15 results** (`results.DEFAULT_TOP_K`, was 500). Loading the texts of 500 rows was about half the database time. The dense methods still produce their own lists (cosine 20, rerank 10, RRF 50) and are cut to 15.
- **Dense filters apply before the cut.** `semantic-rerank`, `cosine-similarity` and `semantic-rrf` used to filter by grade or book after taking their top results, so a filtered search could return fewer than a page (4 instead of 10 in the audit). The allowed ids now go in as `restrict`. Results of filtered dense searches change; re-run evaluations that use filters.
- **Not done, by decision:** no vector index (exact search is about 5.6 ms at 33K x 64; HNSW or IVFFlat would save under 5 ms and lose exactness), no whole-response cache, no `pg_stat_statements`. Findings left open: `hybrid_prf` is not registered as a method; unrestricted BM25 + TF-IDF hybrids aggregate every matching hadith in Python; `ix_hadiths_Book` and `ix_hadiths_Normalized_Grade` are unused by the search path (check `pg_stat_user_indexes` on the live database before dropping).

## Still open

- Nothing has been pushed and no PR exists. Everything is on local branches.
- Blue/green (item 21) was tested with a fake `docker` and stub apps, not with the real image on a real host. There is no automatic promotion on error rate because the app exports no metrics.
- The sign-in limit is per address and not a hard lockout, JWTs cannot be revoked, and the token is kept in `localStorage`.
- Some dependency findings are unfixed, and the Docker image was not rebuilt or rescanned after the PostgreSQL and deploy changes.
- Model promotion (item 40) has not been run against a real MLflow server or the real corpus.
- CI has no job for the PostgreSQL tests: they need a pgvector database (see the quick start).

## First run on a new machine

What was done to get a working site, and what you still need:

1. `.env` with `POSTGRES_PASSWORD`, `AUTH_SECRET` and, to serve keyword search, `APP_MODE=search`, `FEATURE_ANNOTATION=false`, `FEATURE_BENCHMARK=false`, `FEATURE_DENSE_RETRIEVAL=false`. Compose passes these through (defaults: annotation mode).
2. `docker build -t hadith-search:blue .`, then `tools/deploy.sh init`.
3. Load the corpus and the BM25 index inside the image, with a clone of the LK corpus mounted (the image has no git): `python scripts/build_all.py --force --skip-embeddings` with `LK_HADITH_CORPUS_PATH` set. It took about 50 minutes on 4 CPU cores, almost all of it Arabic preprocessing. Result: 33,064 hadiths and 1.4 million postings.
4. `Section_Number` and `Hadith_Number` are text, because the corpus has ranges such as `622 -623` (129 rows) and `5, 6` (153 rows).

**Missing on this machine:** `backend/data/queries.json` (the 20 evaluation queries) is git-ignored and is not in the GitHub repository, so sign-up, annotation and the benchmark fail without it (sign-up returns a 500). Get it from Marawan and copy it into the `hadith-search_hadith-data` volume, then set `FEATURE_ANNOTATION=true` and `FEATURE_BENCHMARK=true`. Semantic search needs `scripts/build_embeddings.py`, which needs a GPU to be practical.

## Quick start after pulling

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/pre-commit install
.venv/bin/python -m pytest
cp .env.example .env    # then fill in DATABASE_URL, POSTGRES_PASSWORD, AUTH_SECRET and anything else you need
docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=test-only-password -e POSTGRES_DB=hadith_test pgvector/pgvector:pg17   # for the tests
tools/test-nginx.sh     # nginx limits and blue/green routing (needs Docker)
```

The commit list, oldest first: `360399f` pre-commit hooks, `4d800cf` backend rewrite, `55d9704` tests and tooling, `92759cf` Docker stages and non-root user, `4b963eb` build caching, `8535ee5` library upgrades, `ea50b33` image scans, `56d8081` Hadolint, health check and labels, `5964375` and `1306ddc` this note, `1981518` REST routes and tokens, `65f4c77` tests for them, `111fb0d` frontend on the new URLs, `b88692b` docs for the REST changes, `a5aac74` security fixes, `bfda508` CI pins and dependency bumps. Later commits, newest last: the PostgreSQL move (`74f0ec6`, `50c892b`, `d4c9fec`, `367184f`), nginx and the sign-in limit (`a8bfda0`, after removing SlowAPI in `8dbcaed`), then blue/green and canary releases (`331dada`, item 21) and the note update for them (`3696e6b`). The sign-in limit went through several changes (`fa9260c`, `a4a5434`, `fb96e2b`) before settling on nginx. The last edit to this note is the commit after those.
