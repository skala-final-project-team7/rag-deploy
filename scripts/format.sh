#!/usr/bin/env bash
# 작성자 : 최태성
# 담당 영역 : rag
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Running format from $ROOT_DIR"

if [ ! -f "./pyproject.toml" ] && [ ! -f "./requirements.txt" ]; then
  echo "No root Python project marker (pyproject.toml / requirements.txt). Skipping format."
  echo "==> Format completed"
  exit 0
fi

if command -v ruff >/dev/null 2>&1; then
  ruff format .
  ruff check . --fix
else
  echo "ruff not found. Install with dev dependencies before running format."
  exit 1
fi

echo ""
echo "==> Format completed"
