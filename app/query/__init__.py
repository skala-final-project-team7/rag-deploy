"""app.query — Query 파이프라인.

사용자 질문 + BFF 가 전달한 userId/groups 를 받아 검증된 답변과 출처를 반환한다.
모든 검색 호출에 ACL 필터가 시스템 단에서 강제 적용된다.

단계 및 분류 (docs/rag-pipeline-design.md §6):
- acl.py        ACL Pre-filtering [Pipeline]  userId/groups → Qdrant 필터 + @enforce_acl 데코레이터
- history.py    멀티턴 히스토리 관리자 [Agent]  보존/삭제/검색스킵 판단 (GPT-4o-mini, 최근 5턴)
- router.py     질의 라우터 [Agent]  단일 LLM 호출 = Intent + Query Rewrite + Filter Builder
- search.py     Multi-Pool Hybrid Search [Pipeline]  3 Pool 병렬 + RRF + 가중 합산 → Top-20
- rerank.py     Cross-Encoder 재순위화 [Pipeline]  ms-marco-MiniLM-L-12, Top-20 → Top-5
- generator.py  답변 생성기 [Agent]  의도별 프롬프트 + GPT-4o + SSE 스트리밍 + Function Calling
- verifier.py   답변 검증 [Pipeline + Agent]  1단계 규칙 매칭 → FLAG → 2단계 LLM 평가자
- formatter.py  응답 포맷터 [Pipeline]  검증된 답변·출처·검증 결과 → UI JSON (docs/api-spec.md)

구현 상태:
- acl.py        extract_principal / build_acl_filter / @enforce_acl [feature7]
- history.py    manage_history — 멀티턴 히스토리 관리자 통합 어댑터 노드
                [feature8 통합] (vendoring한 history_manager_agent 패키지를 RagState에 연결)
- router.py     manage_router — 질의 라우터 통합 어댑터 노드
                [Agent 통합 1/4] (vendoring한 query_routing_agent 패키지를 RagState에 연결)
- search.py     reciprocal_rank_fusion / merge_pools / select_top_candidates /
                fuse_and_rank — Hybrid Search 핵심 로직 [feature9-A]
- rerank.py     select_reranked / RerankResult — Cross-Encoder 재순위화 선정 로직 [feature9-A]
- generator.py  manage_generator — 답변 생성기 통합 어댑터 노드
                [Agent 통합 2/4] (vendoring한 answer_generation_agent 패키지를 RagState에 연결)
- verifier.py   verify_answer_rules / RuleVerificationResult — 답변 검증 1단계 규칙 매칭
                [feature10-Pipeline]
- verifier_evaluator.py  manage_verifier_evaluator — 답변 검증 2단계 LLM 평가자
                통합 어댑터 [Agent 통합 3/4] (vendoring한 answer_verification_agent
                패키지의 evaluator 모듈을 SentenceCheck → Verification 으로 변환)
- formatter.py  format_response — 검증된 답변·출처·검증 결과를 QueryResponse로 변환
                [feature11-Pipeline]
"""

from app.query.acl import (
    ACLViolationError,
    Principal,
    PrincipalExtractionError,
    build_acl_filter,
    enforce_acl,
    extract_principal,
)
from app.query.formatter import format_response
from app.query.generator import manage_generator
from app.query.history import manage_history
from app.query.rerank import RerankResult, select_reranked
from app.query.router import manage_router
from app.query.search import (
    fuse_and_rank,
    merge_pools,
    reciprocal_rank_fusion,
    select_top_candidates,
)
from app.query.verifier import RuleVerificationResult, SentenceCheck, verify_answer_rules
from app.query.verifier_evaluator import manage_verifier_evaluator

__all__ = [
    "ACLViolationError",
    "Principal",
    "PrincipalExtractionError",
    "RerankResult",
    "RuleVerificationResult",
    "SentenceCheck",
    "build_acl_filter",
    "enforce_acl",
    "extract_principal",
    "format_response",
    "fuse_and_rank",
    "manage_generator",
    "manage_history",
    "manage_router",
    "manage_verifier_evaluator",
    "merge_pools",
    "reciprocal_rank_fusion",
    "select_reranked",
    "select_top_candidates",
    "verify_answer_rules",
]
