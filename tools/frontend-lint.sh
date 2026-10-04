#!/usr/bin/env bash
# Frontend lint with a ceiling: the project has known eslint findings (6 errors, 1 warning), so
# plain `npm run lint` always fails. This fails only when there are MORE than that. Lower the
# numbers when findings are fixed. Run from anywhere; needs `npm ci` done in frontend/.
set -euo pipefail
max_errors=${MAX_ERRORS:-6}
max_warnings=${MAX_WARNINGS:-1}
cd "$(dirname "$0")/../frontend"
report=$(mktemp)
trap 'rm -f "$report"' EXIT
npx --no-install eslint . -f json >"$report" || true
node -e '
const r = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
const errors = r.reduce((n, f) => n + f.errorCount, 0);
const warnings = r.reduce((n, f) => n + f.warningCount, 0);
const [maxE, maxW] = [Number(process.argv[2]), Number(process.argv[3])];
console.log(`eslint: ${errors} errors (max ${maxE}), ${warnings} warnings (max ${maxW})`);
process.exit(errors > maxE || warnings > maxW ? 1 : 0);
' "$report" "$max_errors" "$max_warnings"
