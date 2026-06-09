"""질의 라우터 — query-routing-agent 통합 어댑터 노드 [Agent].

--------------------------------------------------
작성자 : 최태성
작성목적 : Agent 담당자가 전달한 Query Routing Agent(vendoring한 ``query_routing_agent``
          패키지)를 RAG Query 파이프라인에 통합한다. 파일 기반 CLI workflow 대신 패키지의
          조립 가능한 로직 함수(normalize → classify → rewrite → filter/weight)를
          in-process로 호출해, RagState ↔ agent 스키마를 잇는 어댑터 노드를 제공한다
          (rag-pipeline-design.md §4.4, ai-agent/query-routing-agent/query-routing-agent.md).
작성일 : 2026-05-18
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-18, 최초 작성, Agent 통합 1/4 — manage_router 어댑터 노드
  - 2026-05-19, feature12 LangGraph config 키워드 충돌 fix — LangGraph 가 노드
    시그니처의 ``config`` 키워드를 자체 ``RunnableConfig`` (dict) 로 자동 주입
    하는 충돌을 generator/verifier 와 동일 패턴으로 해소. ``config`` 인자를
    placeholder 로 유지하고 agent ``QueryRoutingConfig`` 는 ``routing_config``
    keyword-only 인자로 분리한다. 외부 wiring (``query_graph.py`` partial)
    도 ``routing_config=`` 로 갱신. 이전 시그니처에서는 LangGraph 가 주입한
    RunnableConfig dict 가 agent normalize_routing_input 에 전달돼 RoutingProvider
    Error 가 발생하고 매번 OPERATION_GUIDE fallback 으로 떨어지던 회귀를 해소.
  - 2026-05-19, feature17a — ``intent_classification_total{intent}`` Prometheus
    카운터 inc. feature16 smoke 발견 #2 (4종 질의 모두 운영가이드 분류) 의
    분포 가시화 + 설계서 §6.1 의도 분류 정확도 90% 임계 관측 지점. 정상 분기
    는 분류된 intent enum 값, fallback 분기 (``_apply_fallback``) 는 라벨
    ``"fallback"`` 으로 inc.
--------------------------------------------------
[호환성]
  - Python 3.11.x, Pydantic 2.7+
  - NOTE: query_routing_agent 는 ai-agent 저장소에서 vendoring 한 별도 패키지이며
          무수정 보존한다. 본 어댑터만 RAG 컨벤션을 따른다. LLM provider 기본값은
          FakeRoutingLLMProvider (PoC·테스트) 이며, 실제 OpenAIRoutingLLMProvider 를
          주입할 수 있다. provider 호출 실패 시 stub 정합으로 OPERATION_GUIDE fallback.
--------------------------------------------------
"""

from typing import Any

from app.ingestion.vector_store import CONTENT_POOL, LABEL_POOL, TITLE_POOL
from app.metrics import intent_classification_total
from app.schemas.enums import Intent, LlmModel
from app.schemas.rag_state import RagState
from query_routing_agent.config import QueryRoutingConfig
from query_routing_agent.llm import (
    FakeRoutingLLMProvider,
    RoutingLLMProvider,
    classify_intent,
)
from query_routing_agent.routing import (
    build_filter_and_pool_weights,
    normalize_routing_input,
    rewrite_queries,
)
from query_routing_agent.schemas import (
    HistoryDecisionLabel,
    IntentLabel,
)

# IntentLabel(agent enum, 영어 snake_case) → Intent(rag enum, 한국어) 매핑 표.
# UNKNOWN 은 rag Intent 에 대응값이 없으므로 stub 정합으로 OPERATION_GUIDE 로 fallback.
_INTENT_MAP: dict[IntentLabel, Intent] = {
    IntentLabel.INCIDENT_RESPONSE: Intent.INCIDENT_RESPONSE,
    IntentLabel.OPERATIONS_GUIDE: Intent.OPERATION_GUIDE,
    IntentLabel.POLICY_PROCEDURE: Intent.POLICY_PROCEDURE,
    IntentLabel.HISTORY_LOOKUP: Intent.HISTORY_LOOKUP,
    IntentLabel.UNKNOWN: Intent.OPERATION_GUIDE,
}

# Agent 의 fallback intent — provider 실패·conversation_id 미주입 등 안전 fallback 경로에서
# 사용한다. stub 의 OPERATION_GUIDE 기본값과 정합.
_FALLBACK_INTENT = Intent.OPERATION_GUIDE

# stub 정합 — OPERATION_GUIDE 의 의도별 Pool 가중치 (rag-pipeline-design.md §6 4.5).
# Agent provider 가 응답하지 못해 fallback 분기를 탈 때만 사용.
_FALLBACK_POOL_WEIGHTS: dict[str, float] = {
    TITLE_POOL: 0.2,
    CONTENT_POOL: 0.7,
    LABEL_POOL: 0.1,
}

# PoC fake provider 가 응답할 기본 routing decision payload — operations_guide intent.
# parse_routing_llm_response 가 받는 schema 정합: intent / confidence / reason / (옵션)
# expanded_queries 힌트. expanded_queries 가 없으면 rewrite_queries 가 deterministic
# fallback 으로 채워 RagState.rewritten_queries 가 비어있지 않음을 보장한다.
_DEFAULT_FAKE_RESPONSE: dict[str, Any] = {
    "intent": IntentLabel.OPERATIONS_GUIDE.value,
    "confidence": 1.0,
    "reason": "Default fake provider classification (no LLM configured).",
}


def manage_router(
    state: RagState,
    config: Any = None,  # noqa: ARG001 — LangGraph 가 RunnableConfig dict 를 주입할 자리. 무시.
    *,
    provider: RoutingLLMProvider | None = None,
    routing_config: QueryRoutingConfig | None = None,
) -> RagState:
    """질의 라우터 노드 — 라우팅 의도·확장 쿼리·메타필터·Pool 가중치를 채운다.

    vendoring 한 query-routing-agent 의 로직 함수(정규화 → intent 분류 → query rewrite
    → metadata filter / pool weights)를 in-process 로 호출한다. 그 출력을 RagState 의
    ``intent`` / ``rewritten_queries`` / ``metadata_filters`` / ``pool_weights`` /
    ``target_llm`` 필드에 담는다. ``query`` 는 원문 그대로 두어 비파괴적이며, 상류 노드의
    결과(``history_decision`` / ``acl_filter`` 등)는 보존한다.

    Args:
        state: Query 파이프라인 상태. ``query`` / ``user_id`` / ``groups`` /
            ``conversation_id`` / ``history_decision`` 를 읽는다.
        config: LangGraph 가 노드에 자동 주입하는 ``RunnableConfig`` (dict). 본
            어댑터는 사용하지 않으며 무시한다 — keyword name 충돌 회피 목적의
            placeholder. agent 의 ``QueryRoutingConfig`` 는 ``routing_config``
            인자로 받는다 (LangGraph 가 ``config`` 키워드를 자체 RunnableConfig
            로 override 하기 때문에 분리, generator/verifier 와 동일 패턴).
        provider: 라우팅 분류용 LLM provider. None 이면 FakeRoutingLLMProvider 를 쓴다
            (PoC·테스트). 실제 운영에서는 OpenAIRoutingLLMProvider 를 주입한다.
        routing_config: Query Routing 실행 설정. None 이면 기본값 (model=
            "configurable", temperature=0.0, default_query_count=3,
            max_query_count=5) 을 사용한다.

    Returns:
        ``intent`` / ``rewritten_queries`` / ``metadata_filters`` / ``pool_weights`` /
        ``target_llm`` 가 채워진 RagState (입력 state 를 갱신해 반환).
    """
    selected_config = routing_config or QueryRoutingConfig()
    selected_provider = provider or FakeRoutingLLMProvider(_DEFAULT_FAKE_RESPONSE)

    # 안전 fallback: conversation_id 가 없으면 Agent 호출 자체를 회피한다 (정규화 단계에서
    # required 로 실패하기 때문). stub 과 동일한 OPERATION_GUIDE 분기로 떨어진다.
    if not state.conversation_id:
        return _apply_fallback(state)

    payload = _build_routing_input_payload(state)

    try:
        normalized = normalize_routing_input(payload)
        classification = classify_intent(
            normalized_input=normalized,
            config=selected_config,
            provider=selected_provider,
        )
        rewrite_result = rewrite_queries(
            normalized_input=normalized,
            classification=classification,
            config=selected_config,
        )
        filter_result = build_filter_and_pool_weights(
            normalized_input=normalized,
            intent=classification.intent,
            config=selected_config,
        )
    except Exception:  # noqa: BLE001 — Agent provider/parsing 실패는 안전 fallback 으로 흡수.
        return _apply_fallback(state)

    state.intent = _map_intent(classification.intent)
    state.rewritten_queries = list(rewrite_result.expanded_queries)
    state.metadata_filters = filter_result.metadata_filter.to_dict()
    state.pool_weights = _map_pool_weights(filter_result.pool_weights)
    state.target_llm = LlmModel.GPT_4O
    # feature17a — 라우터 의도 분류 분포 메트릭 (설계서 §6.1 + smoke 발견 #2 분석).
    intent_classification_total.labels(intent=state.intent.value).inc()
    return state


def _build_routing_input_payload(state: RagState) -> dict[str, Any]:
    """RagState 를 query-routing-agent 의 routing input dict 로 변환한다.

    History Manager 단계의 산출물(``state.history_decision``) 이 있으면 그 값을 그대로
    전달하고, 없으면 ``new_topic`` 안전 기본값을 쓴다.

    ``query`` 는 의도 분류·query rewrite(→검색 임베딩)의 기준이 되는 질의다. 히스토리
    관리자가 만든 ``contextualized_question`` 이 있으면 그것을(없으면 원문 ``state.query``)
    사용해, 후속 질문("그건 언제야?" 등)이 직전 맥락을 반영한 self-contained 질의로
    검색·재작성되도록 한다. ``original_question`` 은 사용자가 입력한 원문(``state.query``)을
    그대로 보존해 LLM 의도 프롬프트와 로그에서 원 발화를 구분할 수 있게 한다.
    """
    history_decision_label = HistoryDecisionLabel.NEW_TOPIC.value
    preserved_context: dict[str, Any] = {"summary": "", "entities": [], "turn_refs": []}
    reset_required = False
    contextualized_query = state.query
    if state.history_decision is not None:
        history_decision_label = _normalize_history_decision_value(state.history_decision.decision)
        if state.history_decision.preserved_context:
            preserved_context = _normalize_preserved_context_payload(
                state.history_decision.preserved_context
            )
        reset_required = bool(state.history_decision.reset_required)
        # 후속 질문이면 contextualized_question 을 검색·rewrite 기준 query 로 사용(없으면 원문).
        if state.history_decision.contextualized_question:
            contextualized_query = state.history_decision.contextualized_question

    return {
        "conversation_id": state.conversation_id or "",
        "user_id": state.user_id,
        "original_question": state.query,
        "query": contextualized_query,
        "history_decision": history_decision_label,
        "preserved_context": preserved_context,
        "reset_required": reset_required,
        "metadata": {
            "groups": list(state.groups),
            "space_keys": [],
        },
    }


def _normalize_history_decision_value(value: str) -> str:
    """Agent 의 HistoryDecisionLabel 허용값(follow_up/new_topic/ambiguous) 으로 정규화한다.

    History Manager 가 unknown-safe 문자열을 흘려보내는 경우, agent normalization 이
    ambiguous 로 fallback 하므로 본 어댑터는 원문을 그대로 전달한다.
    """
    if not value:
        return HistoryDecisionLabel.NEW_TOPIC.value
    return value


def _normalize_preserved_context_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """HistoryDecision.preserved_context dict 를 agent PreservedContext payload 로 변환."""
    return {
        "summary": str(payload.get("summary") or ""),
        "entities": list(payload.get("entities") or []),
        "turn_refs": list(payload.get("turn_refs") or []),
    }


def _map_intent(intent: IntentLabel | str) -> Intent:
    """Agent IntentLabel 을 rag Intent enum 으로 매핑한다 (UNKNOWN 은 OPERATION_GUIDE)."""
    if isinstance(intent, IntentLabel):
        return _INTENT_MAP.get(intent, _FALLBACK_INTENT)
    try:
        return _INTENT_MAP[IntentLabel(intent)]
    except (KeyError, ValueError):
        return _FALLBACK_INTENT


def _map_pool_weights(pool_weights: Any) -> dict[str, float]:
    """Agent PoolWeights(title/content/label) 을 rag Pool 이름 키로 변환한다."""
    return {
        TITLE_POOL: float(pool_weights.title),
        CONTENT_POOL: float(pool_weights.content),
        LABEL_POOL: float(pool_weights.label),
    }


def _apply_fallback(state: RagState) -> RagState:
    """provider 실패·정규화 실패·conversation_id 미주입 시 stub 정합 안전 fallback.

    rag-pipeline-design.md §8 안전 기본값 — intent=OPERATION_GUIDE, rewritten_queries=
    [원본 쿼리], pool_weights=OPERATION_GUIDE 가중치, target_llm=GPT_4O, metadata_filters=
    None. stub 의 동작과 정합하여 회귀 보호 + 그래프 흐름 보장.
    """
    state.intent = _FALLBACK_INTENT
    state.rewritten_queries = [state.query]
    state.pool_weights = dict(_FALLBACK_POOL_WEIGHTS)
    state.target_llm = LlmModel.GPT_4O
    state.metadata_filters = None
    # feature17a — fallback 경로는 별도 라벨 "fallback" 으로 inc. 운영 시 라우터
    # provider 호출 실패 빈도 / conversation_id 누락 빈도 등을 모니터링한다.
    intent_classification_total.labels(intent="fallback").inc()
    return state


__all__ = ["manage_router"]
