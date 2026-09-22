#!/usr/bin/env bash
# Build the production ZIP deliverable: full source, docs and assets,
# excluding caches, node_modules, venvs and build output.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${1:-pharmacy_ai_os.zip}"
STAMP="$(date +%Y-%m-%d)"

rm -f "$OUT"
zip -r "$OUT" . \
  -x ".git/*" \
  -x "*/.git/*" \
  -x ".venv/*" \
  -x "*/.venv/*" \
  -x "*/node_modules/*" \
  -x "*/__pycache__/*" \
  -x "*/.venv/*" \
  -x "*/dist/*" \
  -x "*/uploads/*" \
  -x "*/.pytest_cache/*" \
  -x "*/.DS_Store" \
  -x "*.pyc" \
  -x "$OUT"

echo "---- $OUT ----"
unzip -l "$OUT" | tail -3
echo "Built $STAMP"
