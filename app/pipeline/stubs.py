"""Agent stub 3종 — 라우터·답변 생성기·검증 2단계 LLM 평가자 fake [Agent stub].

--------------------------------------------------
작성자 : 최태성
작성목적 : feature11 통합 — Agent 담당자가 추후 전달하는 라우터·답변 생성기·검증
          2단계 LLM 평가자를 그래프 조립 단계에서 fake로 대체한다. Agent 코드 전달
          시 교체 지점이 한 곳에 모이도록 본 모듈에 격리한다 (`app/CLAUDE.md` §1
          Agent 분류, `docs/ai/current-plan.md` feature8·10·11 통합 메모).
작성일 : 2026-05-18
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-18, 최초 작성, feature11 통합 — router_stub / generator_stub /
    verify_llm_evaluator_stub. 모두 (state) -> state 또는 (kw) -> verifications 시그니처.
  - 2026-05-18, feature6 Phase 4 — document_analyzer_stub 추가. Ingestion 그래프의
    Agent 노드(문서 분석기) 자리에 들어가며 설계서 §8 fallback 정합으로 doc_type="operation"
    기본값을 채운다. Agent 담당자 코드 전달 시 교체.
  - 2026-05-18, Agent 통합 1/4 — query-routing-agent 어댑터(``app/query/router.py``)
    가 라우터 자리에 wiring 완료. router_stub 자체는 회귀 보호·PoC fallback 용도로
    보존 (QueryGraphDeps.router_node 의 default 는 manage_router 로 변경).
  - 2026-05-19, Agent 통합 2/4 — answer-generation-agent 어댑터(``app/query/
    generator.py``)가 답변 생성기 자리에 wiring 완료. generator_stub 자체는 회귀
    보호용으로 보존 (QueryGraphDeps.generator_node 의 default 는 manage_generator
    로 변경).
  - 2026-05-19, Agent 통합 3/4 — answer-verification-agent 어댑터(``app/query/
    verifier_evaluator.py``)가 검증 2단계 LLM 평가자 자리에 wiring 완료.
    verify_llm_evaluator_stub 자체는 회귀 보호용으로 보존 (QueryGraphDeps
    .verify_llm_evaluator 의 default 는 manage_verifier_evaluator 로 변경).
--------------------------------------------------
[호환성]
  - Python 3.11.x
  - 외부 의존성 0 — Pydantic + StrEnum만 사용.
  - NOTE: 본 모듈은 PoC·테스트용 fake다. 실제 Agent 코드 전달 진척에 따라 다음과
          같이 교체된다:
            * router_stub → ``app/query/router.py`` 의 ``manage_router`` (Agent 통합 1/4
              완료, 2026-05-18). 본 stub 은 회귀 보호용으로 보존.
            * generator_stub → ``app/query/generator.py`` 의 ``manage_generator``
              (Agent 통합 2/4 완료, 2026-05-19). 본 stub 은 회귀 보호용으로 보존.
            * verify_llm_evaluator_stub → ``app/query/verifier_evaluator.py`` 의
              ``manage_verifier_evaluator`` (Agent 통합 3/4 완료, 2026-05-19).
              본 stub 은 회귀 보호용으로 보존.
            * document_analyzer_stub → ``app/pipeline/ingestion_graph.py`` 의
              ``manage_document_analyzer`` (Agent 통합 4/4 완료, 2026-06-04).
              본 stub 은 회귀 보호용으로 보존.
          본 파일 자체는 Agent 코드를 포함하지 않는다 (담당 영역 분리 — `app/CLAUDE.md`
          "담당 범위를 벗어난 파일은 수정하지 않는다").
--------------------------------------------------
"""

from app.ingestion.vector_store import CONTENT_POOL, LABEL_POOL, TITLE_POOL
from app.query.verifier import SentenceCheck
from app.schemas.chunk import Chunk
from app.schemas.enums import DocType, Intent, LlmModel, VerificationStatus
from app.schemas.rag_state import IngestionState, RagState
from app.schemas.response import Verification

# 의도별 Pool 가중치 — rag-pipeline-design.md §6 4.5.
# router_stub은 OPERATION_GUIDE 가중치를 fallback으로 사용한다 (rag-pipeline-design.md §8).
_OPERATION_POOL_WEIGHTS: dict[str, float] = {
    TITLE_POOL: 0.2,
    CONTENT_POOL: 0.7,
    LABEL_POOL: 0.1,
}


def router_stub(state: RagState) -> RagState:
    """질의 라우터 [Agent] fake — rag-pipeline-design.md §8 안전 기본값을 따른다.

    실 라우터(`feat: query_router_agent` Agent 담당자 영역)가 GPT-4o-mini Function
    Calling으로 intent / Query Rewriter / Filter Builder / Pool 가중치를 동시에
    채우는 자리에, 본 stub은 LLM 타임아웃 fallback 정합으로 다음 값을 채운다:
      - `intent = OPERATION_GUIDE`
      - `rewritten_queries = [state.query]` (원본 쿼리 단일)
      - `pool_weights = OPERATION_GUIDE 가중치(0.2 / 0.7 / 0.1)`
      - `target_llm = GPT_4O` (답변 생성기 기본 모델)
      - `metadata_filters = None` (빈 필터)

    `history_decision` 등 상류 노드 결과는 보존한다 (덮어쓰지 않는다).
    """
    state.intent = Intent.OPERATION_GUIDE
    state.rewritten_queries = [state.query]
    state.pool_weights = dict(_OPERATION_POOL_WEIGHTS)
    state.target_llm = LlmModel.GPT_4O
    state.metadata_filters = None
    return state


def generator_stub(state: RagState) -> RagState:
    """답변 생성기 [Agent] fake — top_chunks가 있으면 [#1] 인용을 단 stub 답변을 만든다.

    Agent 통합 2/4 완료 (2026-05-19) — 실 답변 생성기는 ``app/query/generator.py`` 의
    ``manage_generator`` 어댑터로 wiring 되었다. 본 stub 은 회귀 보호용으로 보존된다.
    QueryGraphDeps.generator_node 의 default 는 manage_generator 로 변경되었으나,
    호출자가 deps.generator_node=generator_stub 를 명시 주입하면 그래프 흐름이
    기존과 동일하게 동작함을 보장한다.

    실 답변 생성기(GPT-4o + 의도별 프롬프트 + Function Calling, Agent 담당자 영역)가
    답변 텍스트와 sentence_to_citations를 산출하는 자리에, 본 stub은 검증 1단계가
    동작하도록 [#1] 인용 마커를 포함하는 결정론 stub 답변을 만든다. `top_chunks`가
    비어 있으면 빈 답변 — 그래프 흐름에서는 검색 0건 분기로 우회되므로 도달하지
    않지만, 노드 자체의 방어 처리로 안전하게 둔다.

    `used_llm`은 라우터가 결정한 `target_llm`을 그대로 따르며, 없으면 GPT_4O 기본.
    """
    if state.top_chunks:
        top = state.top_chunks[0]
        title = top.metadata.attachment_filename or top.metadata.page_title
        state.answer = f"[#1] {title} 관련 정보를 다음과 같이 안내합니다."
    else:
        state.answer = ""
    state.used_llm = state.target_llm or LlmModel.GPT_4O
    return state


def verify_llm_evaluator_stub(
    *,
    answer: str,
    top_chunks: list[Chunk],
    suspicious_sentences: list[SentenceCheck],
) -> list[Verification]:
    """검증 2단계 LLM 평가자 [Agent] fake — 의심 문장 모두 SUPPORTED로 판정한다.

    Agent 통합 3/4 완료 (2026-05-19) — 실 2단계 평가자는 ``app/query/
    verifier_evaluator.py`` 의 ``manage_verifier_evaluator`` 어댑터로 wiring 되었다.
    본 stub 은 회귀 보호용으로 보존된다. QueryGraphDeps.verify_llm_evaluator 의
    default 는 manage_verifier_evaluator 로 변경되었으나, 호출자가
    ``deps.verify_llm_evaluator=verify_llm_evaluator_stub`` 를 명시 주입하면 그래프
    흐름이 기존과 동일하게 동작함을 보장한다.

    실 2단계 평가자(GPT-4o-mini, Agent 담당자 영역)가 의심 문장의 SUPPORTED /
    NOT_SUPPORTED를 판정하는 자리에, 본 stub은 보수적으로 모두 SUPPORTED를 반환해
    파이프라인 흐름이 동작함을 보인다. NOT_SUPPORTED 차단 분기는 별도 fake로 시뮬한다.

    Args:
        answer: 생성기가 만든 답변 텍스트 (시그니처 정합 — 실 평가자가 사용).
        top_chunks: 인용 청크 — 실 평가자가 근거 대조에 사용한다.
        suspicious_sentences: 1단계 규칙 매칭에서 의심으로 FLAG된 문장.

    Returns:
        suspicious_sentences와 같은 길이·sentence_id 정합의 Verification 목록.
    """
    # answer / top_chunks는 실 평가자가 사용할 시그니처 정합 — fake는 사용하지 않는다.
    del answer
    del top_chunks
    return [
        Verification(
            sentence_id=check.sentence_id,
            status=VerificationStatus.SUPPORTED,
            cited_chunks=list(check.cited_chunks),
        )
        for check in suspicious_sentences
    ]


def document_analyzer_stub(state: IngestionState) -> IngestionState:
    """문서 분석기 [Agent] fake — 설계서 §8 fallback 정합으로 doc_type 기본값 채움.

    실 문서 분석기(`docs/rag-pipeline-design.md` §3.3)는 2026-06-04 Agent 통합 4/4 로
    ``app/pipeline/ingestion_graph.py`` 의 ``manage_document_analyzer`` (DocumentAnalyzer)
    가 자리에 wiring 되었다. 본 stub 은 회귀 보호용으로 보존하며, LLM 실패 / confidence
    < 0.6 fallback 정합으로 ``doc_type = "operation"`` 을 채운다 (chunking-strategy.md §6
    + design §8).

    Args:
        state: Ingestion 그래프 상태. ``page`` 만 채워져 진입한다.

    Returns:
        ``doc_type`` 이 ``"operation"`` 으로 채워진 상태. ``IngestionState.doc_type`` 은
        ``DocType | AttachmentType | None`` 의미상 본문 doc_type 자리이므로 본 stub 은
        본문에만 적용된다 (첨부의 attachment_type 은 분석기가 아니라 ``analyze_attachment``
        가 결정).
    """
    state.doc_type = DocType.OPERATION.value
    return state
