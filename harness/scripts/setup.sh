#!/bin/bash
# setup.sh - Install token harness git hook
# Run from project root: bash harness/scripts/setup.sh

set -eo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo ".")"

echo "[SETUP] Hermes token harness setup starting..."

# --- 1. Git check ---
if [ ! -d "$ROOT/.git" ]; then
  echo "[WARN] No .git directory found. Run: git init"
  exit 1
fi
echo "[OK] Git repo found"

# --- 2. Install pre-commit hook ---
HOOK_SRC="$ROOT/harness/hooks/pre-commit"
HOOK_DST="$ROOT/.git/hooks/pre-commit"

if [ ! -f "$HOOK_SRC" ]; then
  echo "[FAIL] harness/hooks/pre-commit not found"
  exit 1
fi

cp "$HOOK_SRC" "$HOOK_DST"
chmod +x "$HOOK_DST"

# Fix Windows line endings if needed
if command -v dos2unix &>/dev/null; then
  dos2unix "$HOOK_DST" 2>/dev/null
fi

echo "[OK] pre-commit hook installed -> .git/hooks/pre-commit"

# --- 3. Make scripts executable ---
chmod +x "$ROOT/harness/scripts/lint.sh" 2>/dev/null || true
chmod +x "$ROOT/harness/scripts/garbage-collect.sh" 2>/dev/null || true
chmod +x "$ROOT/harness/rules/compact-context.sh" 2>/dev/null || true
echo "[OK] Script permissions set"

# --- 4. Tool check ---
echo ""
echo "[CHECK] Optional lint tools:"
for tool in autoflake vulture ruff; do
  if command -v "$tool" &>/dev/null; then
    echo "  [OK] $tool found"
  else
    echo "  [WARN] $tool not found (install: pip install $tool)"
  fi
done

echo ""
echo "[DONE] Setup complete. Token harness is active."
