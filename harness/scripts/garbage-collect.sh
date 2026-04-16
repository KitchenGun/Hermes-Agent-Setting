#!/bin/bash
# garbage-collect.sh - Dead code + context bloat detection

set -eo pipefail

ISSUES=0
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo ".")"

# --- 1. Dead code (Python, confidence 90%) ---
if command -v vulture &>/dev/null; then
  find "$ROOT" -name "*.py" \
    -not -path "*/.venv/*" \
    -not -path "*/__pycache__/*" \
    -not -path "*/node_modules/*" | head -100 | while read -r pyfile; do
    vulture "$pyfile" --min-confidence 90 2>/dev/null | while read -r line; do
      echo "DEAD $line"
      ISSUES=$((ISSUES + 1))
    done
  done
fi

# --- 2. Unused imports (Python) ---
if command -v autoflake &>/dev/null; then
  find "$ROOT" -name "*.py" \
    -not -path "*/.venv/*" \
    -not -path "*/__pycache__/*" | head -100 | while read -r pyfile; do
    autoflake --check --remove-all-unused-imports "$pyfile" 2>/dev/null || {
      echo "UNUSED import: $pyfile"
      ISSUES=$((ISSUES + 1))
    }
  done
fi

# --- 3. Context file bloat ---
CLAUDE_MD="$ROOT/CLAUDE.md"
if [ -f "$CLAUDE_MD" ]; then
  lines=$(wc -l < "$CLAUDE_MD")
  if [ "$lines" -gt 60 ]; then
    echo "STALE context-bloat: CLAUDE.md is ${lines} lines (limit: 60)"
    ISSUES=$((ISSUES + 1))
  fi
fi

find "$ROOT/skills" -name "system_prompt.md" -type f 2>/dev/null | while read -r f; do
  lines=$(wc -l < "$f")
  if [ "$lines" -gt 40 ]; then
    echo "WARN context-bloat: $f is ${lines} lines (limit: 40)"
  fi
done

# --- 4. Unused rule files ---
HARNESS_RULES="$ROOT/harness/rules"
if [ -d "$HARNESS_RULES" ]; then
  find "$HARNESS_RULES" -maxdepth 1 -name "*.sh" -type f | while read -r rule_script; do
    rule_name=$(basename "$rule_script" .sh)
    if ! grep -rq "$rule_name" "$ROOT/harness/AGENTS.md" "$HARNESS_RULES"/*.md 2>/dev/null; then
      echo "UNUSED rule: $rule_script (not referenced in AGENTS.md or rules/*.md)"
      ISSUES=$((ISSUES + 1))
    fi
  done
fi

# --- Result ---
if [ $ISSUES -gt 0 ]; then
  echo ""
  echo "[GC] ${ISSUES} issue(s) found"
  exit 1
fi

exit 0
