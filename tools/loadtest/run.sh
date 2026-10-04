#!/usr/bin/env bash
# One load-test run against a running app container, with CPU and memory sampled from docker.
#   tools/loadtest/run.sh LABEL USERS SECONDS [CONTAINER]
# LOADTEST_FILE picks another locustfile (tools/loadtest/locustfile_methods.py: all 8 methods, equal weight).
# Results go to $LOADTEST_OUT/LABEL (default /tmp/loadtest). The container is warmed first so the
# numbers describe steady state, not the first-request loading of the models.
set -euo pipefail
cd "$(dirname "$0")/../.."
label=${1:?label} users=${2:?users} secs=${3:?seconds} container=${4:-hadith-green}
out=${LOADTEST_OUT:-/tmp/loadtest}/$label
mkdir -p "$out"
ip=$(docker inspect "$container" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
host=http://$ip:8000
for spec in "bm25 en prayer" "bm25-prf ar الصلاة" "cosine-similarity ar الصلاة" "semantic-rrf ar الصلاة" "semantic-rerank ar الصلاة"; do
  set -- $spec
  curl -s -o /dev/null -m 120 -G "$host/api/v1/searches" --data-urlencode "q=$3" -d "method=$1&lang=$2"
done
(while true; do docker stats --no-stream --format '{{.CPUPerc}} {{.MemUsage}}' "$container" | sed "s/^/$(date +%s) /"; sleep 2; done >"$out/stats.txt") &
sampler=$!
.venv/bin/locust -f "${LOADTEST_FILE:-tools/loadtest/locustfile.py}" --headless --host "$host" -u "$users" -r 5 -t "${secs}s" \
  --csv "$out/run" --only-summary >"$out/summary.txt" 2>&1 || true
kill "$sampler" 2>/dev/null || true
docker inspect "$container" --format 'oom_killed={{.State.OOMKilled}} restarts={{.RestartCount}}' >>"$out/summary.txt"
python3 - "$out" <<'PY'
import csv, sys
out = sys.argv[1]
rows = list(csv.DictReader(open(out + "/run_stats.csv")))
for r in rows:
    print(f"{r['Name'][:22]:22s} n={r['Request Count']:>5s} fail={r['Failure Count']:>4s} p50={r['50%']:>6s} p95={r['95%']:>6s} p99={r['99%']:>6s} ms  rps={float(r['Requests/s']):6.2f}")
cpu = [float(l.split()[1].rstrip('%')) for l in open(out + "/stats.txt") if len(l.split()) > 2]
def mib(v): return float(v[:-3]) * (1024 if v.endswith("GiB") else 1)
mem = [mib(l.split()[2]) for l in open(out + "/stats.txt") if len(l.split()) > 2]
print(f"container cpu avg={sum(cpu)/len(cpu):.0f}% max={max(cpu):.0f}% (200% = both vCPUs)   mem max={max(mem):.0f} MiB")
PY
tail -1 "$out/summary.txt"
