"""쿼리 → Query LangGraph → SSE 페이로드 CLI 데모 (PoC, 무거운 모델 없음).

--------------------------------------------------
작성자 : 최태성
작성목적 : feature11 통합(Phase 1 + Phase 2)이 끝난 뒤, Pipeline 노드(완료) +
          Agent stub 3종으로 구성된 Query LangGraph 그래프가 끝-끝 동작함을 CLI
          데모로 시각화한다. FastAPI 진입점(app/api/main.py) 없이도 본 스크립트
          한 번으로 build_poc_deps + build_query_graph + run_query를 호출해 SSE
          페이로드(token / sources / verification / meta)를 콘솔에 출력한다.
작성일 : 2026-05-17
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-17, 최초 작성, 코드 리뷰 후속(시연용) — BM25-lite Multi-Pool 검색 데모
  - 2026-05-18, feature11 통합 후속 — BM25-lite + 인메모리 ACL 매칭을
    build_poc_deps + build_query_graph + run_query 끝-끝 호출로 교체. SSE 5종
    페이로드 시각화 + RETRIEVAL_EMPTY / 저신뢰 / 검증 차단 분기 표시
--------------------------------------------------
[호환성]
  - Python 3.11.x
  - 의존성: pydantic / pydantic-settings / beautifulsoup4 / langgraph /
    qdrant-client (이미 main + ingestion extras)
--------------------------------------------------

데이터 흐름:
    samples/*.json → JsonFixtureSourceAdapter → PageObject (92건)
    → chunk_page → Chunk → Fake Dense + Sparse 임베딩 → :memory: Qdrant 적재
    → build_query_graph(deps) → 컴파일된 LangGraph
    → 사용자 query + JWT(stub) → build_acl_filter → RagState
    → run_query(state, graph) → QueryResponse → SSE 페이로드 콘솔 출력

데모와 실제 RAG 차이 (회사 Mac에서 교체될 부분):
    - FakeDenseEmbedder/FakeSparseEmbedder → E5DenseEmbedder + BM25SparseEmbedder
    - FakeCrossEncoderReranker → CrossEncoderRerankerImpl
    - Agent stub 3종(router / generator / verify_llm_evaluator) → 실 Agent 코드
    - 콘솔 출력 → FastAPI SSE(text/event-stream) — app/api/routes.py로 송신

사용법:
    python -m examples.demo_search "EKS 노드 장애 대응 절차"
    python -m examples.demo_search "Kubernetes Helm 설치" --user alice
    python -m examples.demo_search "팀 온보딩" --groups space:ONBOARD
"""

import argparse
import json
import sys
from typing import Any

from app.api.deps import build_poc_deps
from app.pipeline.query_graph import build_query_graph, run_query
from app.query.acl import build_acl_filter
from app.schemas.enums import VerificationStatus
from app.schemas.rag_state import RagState
from app.schemas.response import QueryResponse

_DEFAULT_GROUPS = (
    "space:CLOUD,space:CCC,space:DEVOPS,space:SEC,space:ONBOARD,space:PROJ,space:DATADOG_KR"
)

# 시연 출력 폭. 80자 라인 + 한글이 섞여 시각적으로 적당.
_RULE_WIDTH = 78


def _print_rule(char: str = "=") -> None:
    print(char * _RULE_WIDTH)


def _print_header(args: argparse.Namespace, groups: list[str]) -> None:
    _print_rule()
    print("  RAG Query 그래프 CLI 데모 (PoC — :memory: Qdrant + Fake everything)")
    _print_rule()
    print(f"쿼리        : {args.query}")
    print(f"사용자      : {args.user} (groups={groups})")
    if args.conversation_id:
        print(f"대화 ID     : {args.conversation_id}")
    print()


def _print_meta(response: QueryResponse) -> None:
    """SSE meta 이벤트 정합 — intent / used_llm / feedback_enabled / latency_ms."""
    print("[meta]")
    print(f"  intent           : {response.intent.value}")
    print(f"  used_llm         : {response.used_llm.value}")
    print(f"  feedback_enabled : {response.feedback_enabled}")
    print(f"  latency_ms       : {response.latency_ms}")
    print()


def _print_answer(response: QueryResponse) -> None:
    """SSE token 이벤트 정합 — 본 데모는 1회 송신(전체 답변, Agent 통합 후 확장)."""
    print("[answer]")
    print(f"  {response.answer}")
    print()


def _print_sources(response: QueryResponse) -> None:
    """SSE sources 이벤트 정합 — 출처 카드 배열 (api-spec.md Source 스키마)."""
    print(f"[sources] {len(response.sources)}건")
    if not response.sources:
        print("  (출처 없음)")
        print()
        return
    for rank, source in enumerate(response.sources, start=1):
        preview = source.text_preview[:120].replace("\n", " ")
        print(f"  #{rank}  score={source.score}  [{source.space_key}] {source.title}")
        print(f"        섹션 : {source.path}")
        print(f"        미리보기: {preview}...")
        print(f"        출처 : {source.confluence_url}")
    print()


def _print_verification(response: QueryResponse) -> None:
    """SSE verification 이벤트 정합 — 문장별 검증 결과 + 상태별 카운트 요약."""
    print(f"[verification] {len(response.verification)}건")
    if not response.verification:
        print("  (검증 결과 없음)")
        print()
        return
    counts: dict[str, int] = {
        VerificationStatus.PASS.value: 0,
        VerificationStatus.SUPPORTED.value: 0,
        VerificationStatus.NOT_SUPPORTED.value: 0,
    }
    for item in response.verification:
        counts[item.status.value] += 1
        cited = json.dumps(item.cited_chunks)
        print(f"  - 문장 #{item.sentence_id} → {item.status.value} (인용 {cited})")
    print(
        f"  요약: PASS={counts['PASS']} / SUPPORTED={counts['SUPPORTED']} "
        f"/ NOT_SUPPORTED={counts['NOT_SUPPORTED']}"
    )
    print()


def _print_branch_notes(response: QueryResponse) -> None:
    """api-spec.md 표준 분기 응답을 데모 사용자에게 가시화한다."""
    notes: list[str] = []
    if not response.sources:
        notes.append("RETRIEVAL_EMPTY 분기 — 권한 범위 내 검색 결과 0건. LLM 미호출 표준 응답.")
    elif all(source.score < 20 for source in response.sources):
        notes.append(
            "LOW_CONFIDENCE 분기 — 모든 출처 점수가 임계(20) 미만. feedback_enabled=false."
        )
    if any(item.status is VerificationStatus.NOT_SUPPORTED for item in response.verification):
        not_supported = sum(
            1 for item in response.verification if item.status is VerificationStatus.NOT_SUPPORTED
        )
        ratio = not_supported / len(response.verification)
        if ratio > 0.5:
            notes.append(f"VERIFICATION_BLOCKED 분기 — NOT_SUPPORTED 비율 {ratio:.0%}로 답변 차단.")
    if notes:
        print("[표준 분기 응답]")
        for note in notes:
            print(f"  • {note}")
        print()


def _print_footer() -> None:
    _print_rule()
    print("  ✓ Query 그래프 동작 확인 — Agent 코드 전달 시 QueryGraphDeps 3개 필드만")
    print("    교체하면 라우터 + 답변 생성기 + 검증 2단계 LLM 평가자가 즉시 활성화됨.")
    _print_rule()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="RAG Query 그래프 CLI 데모 (PoC, :memory: Qdrant + Fake everything)"
    )
    parser.add_argument("query", help="검색할 자연어 쿼리")
    parser.add_argument("--user", default="taesung", help="JWT sub (user_id)")
    parser.add_argument(
        "--groups",
        default=_DEFAULT_GROUPS,
        help="콤마 구분 사용자 그룹 (ADR-0002: space:<key>)",
    )
    parser.add_argument(
        "--conversation-id",
        default=None,
        help="대화 컨텍스트 ID (멀티턴 히스토리 관리자가 사용; 미지정 시 단일턴)",
    )
    args = parser.parse_args(argv)

    groups = [group.strip() for group in args.groups.split(",") if group.strip()]
    _print_header(args, groups)

    # 1) PoC deps 부트스트랩 — :memory: Qdrant + Fake everything + samples 자동 인덱싱
    print("[1/3] PoC deps 부트스트랩 (samples 자동 인덱싱)...")
    deps = build_poc_deps()
    print("      ✓ 3 Pool 컬렉션 + Fake 임베더/재순위화기 + samples 인덱싱 완료")
    print()

    # 2) Query LangGraph 컴파일
    print("[2/3] Query LangGraph 컴파일...")
    graph: Any = build_query_graph(deps)
    print("      ✓ manage_history → router → hybrid_search → (empty | rerank → generate → verify)")
    print()

    # 3) RagState 구성 + run_query
    print("[3/3] run_query 실행...")
    acl_filter = build_acl_filter(args.user, groups)
    state = RagState(
        query=args.query,
        user_id=args.user,
        groups=groups,
        conversation_id=args.conversation_id,
        acl_filter=acl_filter,
    )
    response = run_query(state, graph=graph)
    print(f"      ✓ 완료 (latency_ms={response.latency_ms})")
    print()

    _print_rule("-")
    _print_meta(response)
    _print_answer(response)
    _print_sources(response)
    _print_verification(response)
    _print_branch_notes(response)
    _print_footer()
    return 0


if __name__ == "__main__":
    sys.exit(main())
