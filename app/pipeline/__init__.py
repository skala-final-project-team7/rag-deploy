"""app.pipeline — LangGraph 그래프 조립.

app.ingestion / app.query 의 단계별 노드를 LangGraph StateGraph로 연결한다.
각 노드는 단일 책임을 갖고, 노드 입출력 상태는 app.schemas 의 IngestionState / RagState로 통일한다.

모듈:
- query_graph.py      Query 그래프 (ACL → 히스토리 → 라우터 → 검색·재순위화 → 생성 → 검증 → 포맷)
                      + 검색 0건(empty_retrieval) 분기. (RagState.needs_search 는 agent MVP 가
                      검색 스킵 신호를 내지 않아 예약 필드 — 스킵 분기는 없다)
- ingestion_graph.py  Ingestion 그래프 (analyze → chunk → embed_upsert + jobs 기록)
                      문서 분석기[Agent]는 manage_document_analyzer 어댑터(PoC=Fake)
- nodes.py            Pipeline 노드 래퍼 (empty_retrieval / verify_pipeline / after_search_branch)
- stubs.py            Agent stub 4종 (router / generator / verify_llm_evaluator / document_analyzer)
                      — Agent 통합 4/4 완료, 모두 실 어댑터로 교체됨 (stub 은 회귀 보호용 보존)

구현 상태:
- query_graph.py      QueryGraphDeps / build_query_graph / run_query — feature11 통합 (Phase 1).
                      FastAPI SSE 라우트(Phase 2) 완료. Agent 노드는 stubs.py로 교체 가능.
- ingestion_graph.py  IngestionGraphDeps / build_ingestion_graph / run_ingestion /
                      manage_document_analyzer [feature6 Phase 4 + Agent 통합 4/4]
- nodes.py            empty_retrieval_node / verify_pipeline_node / after_search_branch
                      [feature11 통합]
- stubs.py            router_stub / generator_stub / verify_llm_evaluator_stub /
                      document_analyzer_stub [feature11 + feature6 Phase 4] — 4종 모두
                      실 어댑터로 교체 완료, 회귀 보호용 보존
"""

from app.pipeline.ingestion_graph import (
    IngestionGraphDeps,
    build_ingestion_graph,
    manage_document_analyzer,
    run_ingestion,
)
from app.pipeline.nodes import (
    RETRIEVAL_EMPTY_ANSWER,
    after_search_branch,
    empty_retrieval_node,
    verify_pipeline_node,
)
from app.pipeline.query_graph import QueryGraphDeps, build_query_graph, run_query
from app.pipeline.stubs import (
    document_analyzer_stub,
    generator_stub,
    router_stub,
    verify_llm_evaluator_stub,
)

__all__ = [
    "RETRIEVAL_EMPTY_ANSWER",
    "IngestionGraphDeps",
    "QueryGraphDeps",
    "after_search_branch",
    "build_ingestion_graph",
    "build_query_graph",
    "document_analyzer_stub",
    "empty_retrieval_node",
    "generator_stub",
    "manage_document_analyzer",
    "router_stub",
    "run_ingestion",
    "run_query",
    "verify_llm_evaluator_stub",
    "verify_pipeline_node",
]
