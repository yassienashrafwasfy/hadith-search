#!/usr/bin/env bash
# Run the Quran RAG feature on one machine: a pgvector database, two llama.cpp servers (CPU, int4
# GGUF files: controller 270M, writer Falcon-H1-3B) and the app, without building the full app image. See docs/HANDOFF.md item 45.
#
#   tools/quran-local.sh fetch     download Tanzil text, the tafsir dataset and both GGUF models
#   tools/quran-local.sh services  start Postgres (:5433) and the two llama-servers (:8081, :8082)
#   tools/quran-local.sh load      load the Quran and three tafsirs, then build embeddings/passages
#   tools/quran-local.sh app       run the app on http://127.0.0.1:8000 (open /user/quran)
#   tools/quran-local.sh down      stop and remove the three containers (the data volume stays)
#
# Data goes to backend/data (git-ignored). Licences: Tanzil text CC BY 3.0 (must not be changed),
# Tafsir Center dataset CC BY 4.0, controller model Apache-2.0, Falcon-H1 under the Falcon LLM licence (read it on its Hugging Face page).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/backend/data"
PG_PASSWORD="${QURAN_PG_PASSWORD:-quran-local-dev}" # local-only database, not published beyond localhost
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://hadith:${PG_PASSWORD}@localhost:5433/hadith}"
PY="$ROOT/.venv/bin/python"
LLAMA_IMAGE="ghcr.io/ggml-org/llama.cpp:server"
HF="https://huggingface.co"

TAFSIRS=(
  "tafsir_muyassar:tafsir_moyassar:الميسر"
  "tafsir_saadi:tafsir_saadi:السعدي"
  "tafsir_ibn_kathir:tafsir_katheer:ابن كثير"
)

fetch() {
  mkdir -p "$DATA/quran" "$DATA/models"
  curl -fL -o "$DATA/quran/quran-uthmani.txt" \
    "https://tanzil.net/pub/download/index.php?quranType=uthmani&outType=txt-2&agree=true"
  curl -fL -o "$DATA/quran/quran-data.xml" "https://tanzil.net/res/text/metadata/quran-data.xml"
  curl -fL -o "$DATA/quran/tafsir.db" "$HF/datasets/tafsircenter/tafsir-mcp-data/resolve/main/quran.db"
  curl -fL -o "$DATA/models/falcon.gguf" \
    "$HF/tiiuae/Falcon-H1-3B-Instruct-GGUF/resolve/main/Falcon-H1-3B-Instruct-Q4_K_M.gguf"
  curl -fL -o "$DATA/models/controller.gguf" \
    "$HF/AISA-Framework/AISA-AR-FunctionCall-FT-q4_k_m/resolve/main/functiongemma_arabic_v5_Q4_K_M.gguf"
}

run_once() { # name, docker run args...
  local name="$1"
  shift
  if docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
    docker start "$name" >/dev/null
  else
    docker run -d --name "$name" "$@" >/dev/null
  fi
}

services() {
  run_once hadith-quran-pg -p 127.0.0.1:5433:5432 -e POSTGRES_USER=hadith \
    -e POSTGRES_PASSWORD="$PG_PASSWORD" -e POSTGRES_DB=hadith \
    -v hadith-quran-pgdata:/var/lib/postgresql/data pgvector/pgvector:pg17
  run_once hadith-llama-controller -p 127.0.0.1:8081:8080 -v "$DATA/models:/models:ro" --cpus 4 \
    "$LLAMA_IMAGE" -m /models/controller.gguf --host 0.0.0.0 --port 8080 -t 4 -c 2048
  run_once hadith-llama-answerer -p 127.0.0.1:8082:8080 -v "$DATA/models:/models:ro" --cpus 8 \
    "$LLAMA_IMAGE" -m /models/falcon.gguf --host 0.0.0.0 --port 8080 -t 8 -c 4096
}

load() {
  cd "$ROOT/backend"
  local d=data/quran
  "$PY" scripts/quran_load.py --source quran --tanzil $d/quran-uthmani.txt --metadata $d/quran-data.xml
  "$PY" scripts/quran_build.py --source quran --no-summaries
  for spec in "${TAFSIRS[@]}"; do
    IFS=: read -r source table author <<<"$spec"
    "$PY" scripts/quran_load.py --source "$source" --tafsir $d/tafsir.db --author "$author" \
      --tafsir-table "$table" --text-col tafsir --metadata $d/quran-data.xml
    "$PY" scripts/quran_build.py --source "$source" --no-summaries
  done
}

app() {
  cd "$ROOT/backend"
  APP_MODE=annotation FEATURE_QURAN_RAG=true QURAN_WEB_ENABLED=true exec "$ROOT/.venv/bin/uvicorn" main:app \
    --host 127.0.0.1 --port 8000
}

down() { docker rm -f hadith-quran-pg hadith-llama-controller hadith-llama-answerer; }

case "${1:-}" in
  fetch) fetch ;;
  services) services ;;
  load) load ;;
  app) app ;;
  down) down ;;
  *) sed -n '2,11p' "$0" ;;
esac
