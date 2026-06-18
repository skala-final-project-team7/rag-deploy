#!/usr/bin/env bash
# 작성자 : 최태성
# 담당 영역 : rag
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Running lint checks from $ROOT_DIR"

# Root Python project (단독 RAG / 단일 모듈 저장소)
if [ ! -f "./pyproject.toml" ] && [ ! -f "./requirements.txt" ]; then
  echo "No root Python project marker (pyproject.toml / requirements.txt). Skipping lint."
  echo "==> Lint completed"
  exit 0
fi

echo ""
echo "==> root python lint"
if command -v ruff >/dev/null 2>&1; then
  ruff check .
else
  echo "ruff not found. Install with dev dependencies before running lint."
  exit 1
fi

if command -v mypy >/dev/null 2>&1; then
  mypy app
else
  echo "mypy not found. Install with dev dependencies before running lint."
  exit 1
fi

echo ""
echo "==> Lint completed"
