#!/bin/bash
# compact-context.sh - context file line count enforcement
# Created: 2026-04-16 | Token waste from oversized context files
# Auto-fix: No (requires manual trimming)

FIX=${1:-false}
FOUND=0
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo ".")"

# --- 1. CLAUDE.md: max 60 lines ---
CLAUDE_MD="$ROOT/CLAUDE.md"
if [ -f "$CLAUDE_MD" ]; then
  lines=$(wc -l < "$CLAUDE_MD")
  if [ "$lines" -gt 60 ]; then
    echo "ERROR compact-context: CLAUDE.md is ${lines} lines (limit: 60)"
    echo "  Move details to harness/rules/*.md"
    FOUND=$((FOUND + 1))
  fi
fi

# --- 2. system_prompt.md: max 40 lines ---
find "$ROOT/skills" -name "system_prompt.md" -type f 2>/dev/null | while read -r f; do
  lines=$(wc -l < "$f")
  if [ "$lines" -gt 40 ]; then
    echo "WARN compact-context: $f is ${lines} lines (limit: 40)"
    echo "  Remove examples/redundant text to reduce agent token cost"
  fi
done

# --- 3. harness/AGENTS.md: max 60 lines ---
HARNESS_MD="$ROOT/harness/AGENTS.md"
if [ -f "$HARNESS_MD" ]; then
  lines=$(wc -l < "$HARNESS_MD")
  if [ "$lines" -gt 60 ]; then
    echo "ERROR compact-context: harness/AGENTS.md is ${lines} lines (limit: 60)"
    FOUND=$((FOUND + 1))
  fi
fi

[ $FOUND -gt 0 ] && exit 1
exit 0
