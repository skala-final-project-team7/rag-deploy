#!/usr/bin/env bash
# 작성자 : 최태성
# 담당 영역 : rag
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Running tests from $ROOT_DIR"

# Root Python project (단독 RAG / 단일 모듈 저장소)
if [ ! -f "./pyproject.toml" ] && [ ! -f "./requirements.txt" ]; then
  echo "No root Python project marker (pyproject.toml / requirements.txt). Skipping tests."
  echo "==> Tests completed"
  exit 0
fi

echo ""
echo "==> root python tests"
if command -v pytest >/dev/null 2>&1; then
  pytest
else
  echo "pytest not found. Install with dev dependencies before running tests."
  exit 1
fi

echo ""
echo "==> Tests completed"
