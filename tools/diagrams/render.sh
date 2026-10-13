#!/usr/bin/env bash
# Regenerates the diagrams in docs/diagrams from their sources, using npx and a throwaway
# directory (nothing is added to the repo's package.json).
#   tools/diagrams/render.sh schema       docs/diagrams/schema.dbml -> schema.svg (and a dbml2sql check)
#   tools/diagrams/render.sh components   components.mmd -> components.excalidraw + components.svg
#   tools/diagrams/render.sh ai           quran_agent, quran_retrieval, ai_overview .mmd -> .svg (mermaid-cli)
# components needs a Chromium: set CHROME to a chrome/chrome-headless-shell binary
# (for example the one Playwright caches under ~/.cache/ms-playwright).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
docs="$here/../../docs/diagrams"
case "${1:-}" in
schema)
  npx -y -p @dbml/cli dbml2sql --postgres "$docs/schema.dbml" -o /dev/null
  npx -y -p @softwaretechnik/dbml-renderer dbml-renderer -i "$docs/schema.dbml" -o "$docs/schema.svg"
  rm -f dbml-error.log "$docs/dbml-error.log"
  ;;
components)
  : "${CHROME:?set CHROME to a Chromium binary}"
  tmp="$(mktemp -d)"
  cp "$here/entry.js" "$here/run.cjs" "$tmp/"
  (cd "$tmp" && npm init -y >/dev/null \
    && npm i --no-audit --no-fund playwright-core @excalidraw/mermaid-to-excalidraw @excalidraw/excalidraw esbuild react react-dom >/dev/null \
    && npx esbuild entry.js --bundle --outfile=bundle.js --format=iife \
      --define:process.env.NODE_ENV='"production"' --define:process.env.IS_PREACT='"false"' \
      --loader:.woff2=dataurl --loader:.ttf=dataurl --log-level=warning \
    && BUNDLE="$tmp/bundle.js" node run.cjs "$(realpath "$docs/components.mmd")" \
      "$(realpath "$docs")/components.excalidraw" "$(realpath "$docs")/components.svg")
  rm -rf "$tmp"
  ;;
ai)
  : "${CHROME:?set CHROME to a Chromium binary}"
  cfg="$(mktemp)"
  printf '{"executablePath": "%s", "args": ["--no-sandbox", "--disable-setuid-sandbox"]}\n' "$CHROME" >"$cfg"
  for name in quran_agent quran_retrieval ai_overview; do
    npx -y @mermaid-js/mermaid-cli -i "$docs/$name.mmd" -o "$docs/$name.svg" -p "$cfg" -b white -q
  done
  rm -f "$cfg"
  ;;
*) echo "usage: $0 schema|components|ai" >&2; exit 2 ;;
esac
