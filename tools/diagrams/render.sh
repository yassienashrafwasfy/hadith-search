#!/usr/bin/env bash
# Regenerates the diagrams in docs/diagrams from their sources, using npx and a throwaway
# directory (nothing is added to the repo's package.json).
#   tools/diagrams/render.sh schema       docs/diagrams/schema.dbml -> schema.svg (and a dbml2sql check)
#   tools/diagrams/render.sh components   components.mmd -> components.excalidraw + components.svg
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
*) echo "usage: $0 schema|components" >&2; exit 2 ;;
esac
