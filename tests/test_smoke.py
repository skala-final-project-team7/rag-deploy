"""골격 스모크 테스트.

실제 파이프라인 테스트는 docs/ai/workflow.md의 '테스트 우선' 절차에 따라
feature 단위로 추가한다. 이 파일은 패키지 골격이 import 가능한지만 확인한다.
"""

import importlib

PACKAGES = [
    "app",
    "app.schemas",
    "app.adapters",
    "app.llm",
    "app.ingestion",
    "app.ingestion.chunker",
    "app.ingestion.embedder",
    "app.query",
    "app.query.reranker",
    "app.storage",
    "app.pipeline",
    "app.api",
]


def test_app_packages_are_importable() -> None:
    for name in PACKAGES:
        module = importlib.import_module(name)
        assert module is not None
