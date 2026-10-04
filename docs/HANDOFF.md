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

**The schema rule:** both colours run against the same database at the same time, and the app only ever adds tables and columns (`create_all`, no migrations). So a release may add tables and columns, but must not rename or drop anything the previous release reads or writes. Do a rename in two releases: add the new name and write both, then remove the old one in the next release once the old colour is gone. `tests/test_database.py` checks that starting the older release does not remove columns or tables a newer one added. Embeddings are one column per language with no fixed size, so a release that changes the embedding model must re-encode every row before it goes live; a canary cannot mix two models in one column.

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

**Files:** `backend/scripts/export_onnx.py` (new), `backend/scripts/arabic_encoder.py` (new), `backend/scripts/recall_proxy.py` (new), `backend/scripts/loading.py`, `backend/scripts/build_embeddings.py`, `backend/scripts/embedding_store.py`, `backend/services/ranking.py`, `backend/services/retrieval.py`, `backend/routers/search.py`, `backend/scripts/eval_pipeline.py`, `backend/scripts/pooling.py`, `backend/scripts/finetune_eval.py`, `backend/scripts/migrate_to_postgres.py`, `backend/features.py`, `frontend/src/api/useSearchMethods.ts`, `requirements.txt`, tests.

**What changed:** the dense methods (`cosine-similarity`, `semantic-rerank`, `semantic-rrf`) now use `akhooli/sbert-nli-500k-triplets-MB` (ModernBERT, Arabic only, trained with Matryoshka loss, Apache-2.0, revision `73ca7f3`). `scripts/export_onnx.py` downloads it and writes an ONNX file whose graph does the mean pooling, keeps the first 256 values and normalises them. Queries run through ONNX Runtime, so torch is only needed to export. Choices made with the owner: Arabic only, vectors cut to 256, newest opset that passes.

**Opset:** the script tries opsets from the newest ONNX knows (28) down to 17 and keeps the first that ONNX Runtime 1.30 loads and whose output matches the PyTorch model (cosine 0.9999 or better on five Arabic texts, single and padded batch). Opsets 28, 27 and 26 were rejected by ONNX Runtime ("invalid graph" in the attention node). **Opset 25 was accepted, lowest cosine 0.999977.** The result is in `export.json` next to the model.

**Effects you should know about:**

- **English has no semantic search.** The dense methods return HTTP 422 for `lang=en`; `/search-methods` lists `languages` for each method and the picker shows only methods that fit the chosen language.
- **Evaluation:** dense systems are scored on Arabic queries only (`ARABIC_ONLY_SYSTEMS` in `pooling.py`), and pooling skips them for English queries. Their numbers are not comparable with the BM25 rows, which cover all queries, and earlier results made with E5 cannot be reproduced.
- **The model is not in git** (596 MB). Create it with `cd backend && python -m scripts.export_onnx`; it goes to `backend/data/onnx/arabic` (in Docker, the `hadith-data` volume), or to `ARABIC_MODEL_DIR`. Then run `python -m scripts.build_embeddings` (about an hour on 16 CPU cores for 33,064 hadiths). Both steps ran inside the container on this machine.
- **The old E5 vectors are not used.** The `english` column of `hadith_embeddings` stays (blue and green share one schema) but is empty. `migrate_to_postgres.py` no longer copies `.npy` embeddings.
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

## Still open

- Nothing has been pushed and no PR exists. Everything is on local branches.
- Blue/green (item 21) was tested with a fake `docker` and stub apps, not with the real image on a real host. There is no automatic promotion on error rate because the app exports no metrics.
- The sign-in limit is per address and not a hard lockout, JWTs cannot be revoked, and the token is kept in `localStorage`.
- Some dependency findings are unfixed, and the Docker image was not rebuilt or rescanned after the PostgreSQL and deploy changes.
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
