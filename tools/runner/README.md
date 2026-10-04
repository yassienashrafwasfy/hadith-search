# Self-hosted CI runner

`.github/workflows/ci.yml` runs on this machine's runner when it is online and idle, and on GitHub's
own runners otherwise. Nothing here is started or registered for you.

## How the choice is made

The first job (`pick-runner`, always GitHub-hosted) runs `tools/pick-runner.sh`. It uses the
self-hosted runner only when **both** are true:

- the event is trusted: push to main, the weekly schedule, a manual run, or a pull request from a
  branch of this same repository. A pull request from a **fork always runs on GitHub's runners**,
  so outside code never runs on your machine;
- the GitHub API says a runner with the label `hadith-ci` is online and not busy.

Anything else, including a missing token or an API error, means GitHub-hosted. The reason is
written to the job summary.

Limits: the check and the job start are separate steps, so if the runner takes another job in
between, your job waits in the queue for it instead of falling back. If the runner is switched off
while a job is queued for it, that job waits until it is switched on again.

## One-time setup (you do this)

1. **Status token** (read only). GitHub > Settings > Developer settings > Fine-grained tokens >
   this repository only, permission `Administration: Read`. Save it as the repository secret
   `RUNNERSTATUSTOKEN` (no underscores; that is the name the workflow reads) (Settings > Secrets and variables > Actions). Without it every job
   simply runs on GitHub's runners.
2. **Registration token** (write). A second fine-grained token, this repository only,
   `Administration: Read and write`. It goes in `tools/runner/.env.runner` only.
3. Fork pull requests: Settings > Actions > General > "Require approval for all outside
   collaborators" is a good extra guard for the hosted side.

## Start, stop, remove

```bash
cd tools/runner
cp .env.runner.example .env.runner      # fill in REPO_URL and ACCESS_TOKEN
docker compose --env-file .env.runner up -d      # registers the runner (label hadith-ci)
docker compose --env-file .env.runner logs -f runner
docker compose --env-file .env.runner down       # stop; the runner shows offline, jobs go hosted
docker compose --env-file .env.runner down -v    # also delete the Docker cache and build cache
```

The runner is ephemeral: after each job it deregisters and registers again, so no job sees files
from an earlier one. If a stale offline entry remains in Settings > Actions > Runners, remove it
there.

## What to know

- The images are pinned by digest. To update: change the tag and digest in `compose.yml`, `up -d`.
- The Docker daemon is a privileged container (`dind`). That is why untrusted code must never reach
  this runner; do not change the trusted-events rule in `tools/pick-runner.sh` without thinking
  about that.
- `ACCESS_TOKEN` stays inside the runner container's registration step (`UNSET_CONFIG_VARS`); jobs
  do not see it. Rotate it if you ever paste it anywhere.
- Docker image layers are cached in the `buildx-cache` volume (`type=local` in `ci.yml`); the
  GitHub-hosted path uses the `gha` cache.
- Needs about 15 GB of disk for the Docker images (CPU torch, pgvector, trivy, dockle).
