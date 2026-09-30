# Handoff: changes since Marawan's last commit

Marawan's last commit is `93a4ff9` ("Align matn embeddings, training passages, and evaluation"). Everything below was added on top of it in 8 commits: 107 files, about 10,000 lines added and 3,000 removed. All 312 tests pass. The work sits on the branch `chore/dockerfile-hardening` (which builds on `chore/precommit-hooks`) and has not been pushed.

Each change has the same three lines: which files, why this is the normal way to do it, and what you get out of it.

## Read this first

1. **The Jina key was renamed.** The code used to read `JINA_API_KEY2`. It now reads `JINA_API_KEY`. Rename it in your `.env` and in the server settings, or reranking will stop working.
2. **The diff looks bigger than the real change.** Every Python file was reformatted to one style, so many lines only moved or wrapped differently. Read the commit messages first, then the files.
3. **Libraries were upgraded** to fix known security holes: numpy 1.26 to 2.5, transformers 4.43 to 5.17, starlette 0.52 to 1.3, nltk 3.9 to 3.10. The tests pass, but I could not test saved index files (`.pkl`) built with the old numpy, because this copy has no real data. If loading fails, rebuild them. A full fine-tuning run and the real E5 model were also not re-run.
4. **The Docker container no longer runs as root.** It runs as user 10001. If you mount a folder for `backend/data` instead of using a Docker volume, run `chown 10001 <folder>` on it once.

## The changes

### 1. Automated tests

**Files:** `tests/` (22 test files and their saved expected outputs), `pyproject.toml`, `requirements-dev.txt`

**Why it's best practice:** Tests that run without real data, internet or GPU can run anywhere, including on every commit. Fake versions of the E5 model and the Jina API stand in for the real ones.

**Benefit:** You can change code and find out in under half a minute whether something broke. Run them with `.venv/bin/python -m pytest`.

### 2. One code style, checked automatically

**Files:** `pyproject.toml` (ruff and black settings), and a reformat of `backend/` and `tests/`

**Why it's best practice:** When a tool decides spacing and import order, nobody argues about it in reviews, and diffs only show real changes from now on.

**Benefit:** Cleaner reviews. Ruff also catches unused imports and functions that are too complicated (limit: 10 branches).

### 3. Database through models instead of hand-written SQL

**Files:** `backend/database.py`, `backend/models/orm.py`, `backend/routers/annotation.py`, `auth.py`, `kv_pairs.py`, `backend/scripts/kv_generator.py`

**Why it's best practice:** SQL written by hand in strings is easy to get wrong and open to injection. Python model classes are checked by tools and build their own queries. The database calls are also async now, so one slow query no longer blocks the server.

**Benefit:** Fewer bugs and safer queries. A test fails if anyone adds raw SQL again.

### 4. Settings in one place

**Files:** `backend/features.py`, `.env.example`, `backend/main.py`

**Why it's best practice:** Switches for optional parts (annotation, search, dense retrieval, Jina reranking) live in one file and can be flipped with environment variables. `.env.example` lists every variable without holding real keys.

**Benefit:** You can run a light version (annotation only) or the full search stack without editing code. New team members know which variables exist.

### 5. Shorter import paths

**Files:** `backend/models/__init__.py`, `backend/routers/__init__.py`, `backend/scripts/__init__.py`, `backend/lazy_exports.py`

**Why it's best practice:** Code imports from the package (`from models import Hadith`) and does not need to know which file holds the class.

**Benefit:** Files can be moved or split later without breaking every import. Heavy modules still load only when used.

### 6. Smaller functions, split by job

**Files:** new `backend/services/` (retrieval, results, agreement), `backend/startup.py`, `backend/scripts/eval_pipeline.py`; long scripts such as `preprocess.py`, `full_evaluation.py`, `finetune.py`, `evaluation.py` and `search.py` were broken into short functions

**Why it's best practice:** A function that does one thing is easy to read and easy to test. The search code was moved gradually: the new `services/` code wraps the old functions, so the offline scripts that call the old ones still work.

**Benefit:** Bugs are easier to find. Adding a new retrieval method means adding one entry to a list, with no edits inside a large function.

### 7. Checks that the tests are actually good

**Files:** `tools/mutation.sh`, `tools/mutation.pyproject.toml`, `tests/test_mutation_killers.py`; vulture settings in `pyproject.toml`

**Why it's best practice:** Line coverage only says code ran. Mutation testing makes small deliberate breaks in the code and checks that some test fails. Vulture lists code nobody calls.

**Benefit:** We found weak spots and wrote tests for them. Vulture reports no dead code now.

### 8. Pre-commit hooks

**Files:** `.pre-commit-config.yaml`, `.gitignore`, `requirements-dev.txt`

**Why it's best practice:** Checks that run before every commit stop mistakes at the earliest point. Gitleaks looks for passwords and API keys in what you are about to commit. Ruff, black, vulture and Hadolint run too, plus small checks for large files and merge leftovers.

**Benefit:** A leaked key never reaches the repository history, where it would be hard to remove. Run `.venv/bin/pre-commit install` once after cloning.

### 9. Smaller, safer Docker image

**Files:** `Dockerfile`, `.dockerignore`, `docker-entrypoint.sh` (no change, but it was being excluded by mistake)

**Why it's best practice:** The Dockerfile has three stages, so compilers and build tools stay out of the final image. The app runs as a normal user instead of root, so a break-in gets less. Downloaded packages are cached between builds, and slow steps come first so small code edits rebuild fast.

**Benefit:** Faster rebuilds, a smaller attack surface, and the CAMeL Arabic data now really downloads at build time (the old step always failed quietly).

### 10. Security scans and library upgrades

**Files:** `tools/scan-image.sh`, `.github/workflows/docker-scan.yml`, `requirements.txt`

**Why it's best practice:** Trivy checks the image for known security holes and dockle checks it against container good-practice rules. The workflow runs both on every push, every pull request and every Monday, because new holes are found without any code change.

**Benefit:** The first scan found 46 fixable problems. After the library upgrades (see "Read this first") there are none. Run it locally with `docker build --pull -t hadith-search:hardened . && tools/scan-image.sh`.

### 11. Dockerfile lint, health check, labels

**Files:** `Dockerfile`, `.hadolint.yaml`

**Why it's best practice:** Hadolint reads the Dockerfile for mistakes. A health check lets Docker see when the app has stopped answering. Labels record what the image is and which commit built it.

**Benefit:** Docker can restart a hung container by itself. Anyone holding an image can tell which commit it came from.

### 12. Notes for the next person

**Files:** `CLAUDE.md`, `docs/WIKI.md`, this file

**Why it's best practice:** Setup steps and rules written down in the repo stay with the code.

**Benefit:** A new developer, or an AI assistant, can start work without asking what the commands are.

## Quick start after pulling

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/pre-commit install
.venv/bin/python -m pytest
cp .env.example .env    # then fill in JINA_API_KEY and anything else you need
```

The commit list, oldest first: `360399f` pre-commit hooks, `4d800cf` backend rewrite, `55d9704` tests and tooling, `92759cf` Docker stages and non-root user, `4b963eb` build caching, `8535ee5` library upgrades, `ea50b33` image scans, `56d8081` Hadolint, health check and labels.
