#!/bin/bash
# lint.sh - Token harness rule-based linter
# Usage: bash harness/scripts/lint.sh [--fix] [--quiet]

set -eo pipefail

FIX=false
QUIET=false
ERRORS=0
FIXED=0

for arg in "$@"; do
  case $arg in
    --fix)   FIX=true ;;
    --quiet) QUIET=true ;;
  esac
done

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo ".")"
RULES_DIR="$ROOT/harness/rules"

# --- Built-in: unused import check (Python) ---
check_unused_imports() {
  local file="$1"
  if command -v autoflake &>/dev/null; then
    autoflake --check --remove-all-unused-imports "$file" 2>/dev/null || {
      if [ "$FIX" = "true" ]; then
        autoflake --in-place --remove-all-unused-imports "$file"
        FIXED=$((FIXED + 1))
        return 0
      fi
      echo "ERROR unused-import: $file"
      ERRORS=$((ERRORS + 1))
    }
  fi
}

# --- Built-in: dead code check (Python) ---
check_dead_code() {
  local file="$1"
  if command -v vulture &>/dev/null; then
    vulture "$file" --min-confidence 80 2>/dev/null | while read -r line; do
      echo "WARN dead-code: $line"
    done
  fi
}

# --- Run on staged Python files ---
changed_files=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null \
  || find "$ROOT" -name "*.py" -not -path "*/.venv/*" -not -path "*/__pycache__/*" | head -50)

for file in $changed_files; do
  case "$file" in
    *.py)
      check_unused_imports "$file"
      check_dead_code "$file"
      ;;
  esac
done

# --- Run custom rules ---
if [ -d "$RULES_DIR" ]; then
  find "$RULES_DIR" -maxdepth 1 -name "*.sh" -type f | sort | while read -r rule; do
    bash "$rule" "$FIX" 2>&1 || {
      rule_name=$(basename "$rule" .sh)
      echo "ERROR rule-fail: $rule_name"
      ERRORS=$((ERRORS + 1))
    }
  done
fi

# --- Result ---
if [ "$QUIET" = "true" ] && [ $ERRORS -eq 0 ]; then
  exit 0
fi

if [ $ERRORS -gt 0 ]; then
  echo ""
  echo "Lint FAILED: ${ERRORS} error(s) | auto-fixed: ${FIXED}"
  exit 1
fi

exit 0
