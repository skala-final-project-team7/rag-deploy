"""질의 라우터 어댑터 검증 (Agent 통합 1/4) — query-routing-agent ↔ RagState.

manage_router: vendoring 한 query-routing-agent 로직(normalize → classify → rewrite →
filter/weight)을 in-process 로 호출해 라우팅 의도·확장 쿼리·메타필터·Pool 가중치를
채우고 RagState 에 담는다. agent LLM 호출은 FakeRoutingLLMProvider 로 대체.
"""

import json

import pytest

from app.ingestion.vector_store import CONTENT_POOL, LABEL_POOL, TITLE_POOL
from app.query.router import manage_router
from app.schemas.enums import Intent, LlmModel
from app.schemas.rag_state import HistoryDecision, RagState
from query_routing_agent.llm import FakeRoutingLLMProvider


def _state(
    *,
    conversation_id: str | None = "conv-1",
    query: str = "IAM 정책 변경 후 롤백 절차는?",
    groups: list[str] | None = None,
    history_decision: HistoryDecision | None = None,
) -> RagState:
    return RagState(
        query=query,
        user_id="user-1",
        conversation_id=conversation_id,
        groups=groups if groups is not None else ["sre", "platform"],
        history_decision=history_decision,
    )


def _fake(intent: str, *, expanded_queries: list[str] | None = None) -> FakeRoutingLLMProvider:
    payload: dict[str, object] = {
        "intent": intent,
        "confidence": 0.9,
        "reason": f"{intent} 판정",
    }
    if expanded_queries is not None:
        payload["expanded_queries"] = expanded_queries
    return FakeRoutingLLMProvider(payload)


def test_no_conversation_id_shortcuts_to_fallback() -> None:
    # conversation_id 가 없으면 agent 호출 없이 OPERATION_GUIDE 로 안전 fallback.
    result = manage_router(_state(conversation_id=None))
    assert result.intent is Intent.OPERATION_GUIDE
    assert result.rewritten_queries == ["IAM 정책 변경 후 롤백 절차는?"]
    assert result.pool_weights == {TITLE_POOL: 0.2, CONTENT_POOL: 0.7, LABEL_POOL: 0.1}
    assert result.target_llm is LlmModel.GPT_4O
    assert result.metadata_filters is None


def test_operations_guide_intent_default() -> None:
    # 기본 fake provider (operations_guide) — provider=None 분기 회귀 보호.
    result = manage_router(_state())
    assert result.intent is Intent.OPERATION_GUIDE
    assert result.target_llm is LlmModel.GPT_4O
    assert result.rewritten_queries  # deterministic fallback 으로 비어있지 않음.


def test_incident_response_intent_maps_to_korean_enum() -> None:
    result = manage_router(_state(), provider=_fake("incident_response"))
    assert result.intent is Intent.INCIDENT_RESPONSE
    # incident_response 의 default pool weight (title=0.2/content=0.65/label=0.15).
    assert result.pool_weights == {TITLE_POOL: 0.2, CONTENT_POOL: 0.65, LABEL_POOL: 0.15}


def test_policy_procedure_intent_maps_to_korean_enum() -> None:
    result = manage_router(_state(), provider=_fake("policy_procedure"))
    assert result.intent is Intent.POLICY_PROCEDURE
    assert result.pool_weights == {TITLE_POOL: 0.3, CONTENT_POOL: 0.6, LABEL_POOL: 0.1}


def test_history_lookup_intent_maps_to_korean_enum() -> None:
    result = manage_router(_state(), provider=_fake("history_lookup"))
    assert result.intent is Intent.HISTORY_LOOKUP
    assert result.pool_weights == {TITLE_POOL: 0.2, CONTENT_POOL: 0.5, LABEL_POOL: 0.3}


def test_unknown_intent_falls_back_to_operation_guide() -> None:
    # Agent IntentLabel.UNKNOWN 은 rag Intent 에 대응값이 없으므로 OPERATION_GUIDE fallback.
    result = manage_router(_state(), provider=_fake("unknown"))
    assert result.intent is Intent.OPERATION_GUIDE


def test_expanded_queries_hint_is_used_when_provided() -> None:
    hints = ["IAM 정책 롤백 절차", "IAM rollback procedure"]
    result = manage_router(
        _state(),
        provider=_fake("operations_guide", expanded_queries=hints),
    )
    # LLM 힌트가 있으면 정규화 후 first-N 으로 그대로 사용 (default 3 개까지 채워짐).
    assert hints[0] in result.rewritten_queries
    assert hints[1] in result.rewritten_queries


def test_expanded_queries_default_fallback_is_nonempty() -> None:
    # LLM 힌트 없으면 deterministic fallback — rewritten_queries 가 반드시 비어있지 않음.
    result = manage_router(_state(), provider=_fake("operations_guide"))
    assert len(result.rewritten_queries) >= 1
    assert all(isinstance(q, str) and q for q in result.rewritten_queries)


def test_pool_weights_sum_to_one() -> None:
    result = manage_router(_state(), provider=_fake("operations_guide"))
    total = sum(result.pool_weights.values())
    assert total == pytest.approx(1.0)


def test_metadata_filters_includes_groups_via_acl() -> None:
    # groups 가 routing input 의 metadata.groups 로 전달되어 MetadataFilter.acl 에 담긴다.
    result = manage_router(
        _state(groups=["sre", "platform"]),
        provider=_fake("operations_guide"),
    )
    assert result.metadata_filters is not None
    acl = result.metadata_filters["acl"]
    assert acl["user_id"] == "user-1"
    assert sorted(acl["groups"]) == ["platform", "sre"]


def test_provider_failure_falls_back_safely() -> None:
    # provider 가 RuntimeError 를 던지면 안전 fallback (OPERATION_GUIDE) 로 흡수.
    failing_provider = FakeRoutingLLMProvider(RuntimeError("transient provider error"))
    result = manage_router(_state(), provider=failing_provider)
    assert result.intent is Intent.OPERATION_GUIDE
    assert result.rewritten_queries == ["IAM 정책 변경 후 롤백 절차는?"]
    assert result.pool_weights == {TITLE_POOL: 0.2, CONTENT_POOL: 0.7, LABEL_POOL: 0.1}
    assert result.metadata_filters is None


def test_invalid_llm_payload_falls_back_safely() -> None:
    # confidence 누락 등 schema 위반은 ClassificationValidationError 로 흡수돼 fallback.
    invalid_provider = FakeRoutingLLMProvider(
        json.dumps({"intent": "operations_guide", "reason": "missing confidence"})
    )
    result = manage_router(_state(), provider=invalid_provider)
    assert result.intent is Intent.OPERATION_GUIDE
    assert result.metadata_filters is None


def test_history_decision_preserved_context_is_forwarded() -> None:
    # history_decision 의 preserved_context 가 라우터 입력 payload 로 전달되는지 회귀 보호.
    # 단순 동작 검증 — context 가 전달돼도 정상 분기 진행을 확인하면 충분.
    decision = HistoryDecision(
        decision="follow_up",
        contextualized_question="IAM 정책 변경 후 롤백 절차는?",
        preserved_context={
            "summary": "IAM 정책 변경 장애 대응 맥락",
            "entities": ["IAM 정책", "롤백"],
            "turn_refs": ["turn-0", "turn-1"],
        },
        reset_required=False,
        confidence=0.8,
        reason="follow_up 판정",
    )
    result = manage_router(
        _state(history_decision=decision),
        provider=_fake("incident_response"),
    )
    assert result.intent is Intent.INCIDENT_RESPONSE


def test_followup_contextualized_question_drives_routing_query() -> None:
    # 후속 질문: 히스토리 관리자가 만든 contextualized_question 이 라우터의 검색·rewrite·의도
    # 분류 기준 query 로 전달돼 검색에 맥락이 반영되는지, original_question 은 원문 그대로
    # 보존되는지 회귀 보호.
    decision = HistoryDecision(
        decision="follow_up",
        contextualized_question="IAM 정책 변경은 언제 발생했나요?",
        preserved_context={"summary": "", "entities": [], "turn_refs": []},
        reset_required=False,
        confidence=0.9,
        reason="follow_up 판정",
    )
    provider = _fake("operations_guide")
    state = _state(query="그건 언제야?", history_decision=decision)
    result = manage_router(state, provider=provider)

    assert provider.requests, "라우터가 agent provider 를 호출해야 한다."
    forwarded = provider.requests[0]
    # 검색·rewrite·의도 분류 기준 query = contextualized_question.
    assert forwarded.query == "IAM 정책 변경은 언제 발생했나요?"
    # original_question 은 사용자 원문 발화를 보존.
    assert forwarded.routing_input["original_question"] == "그건 언제야?"
    # rewritten_queries(검색 입력)도 contextualized 기준으로 생성된다(원문 단편 아님).
    assert any("IAM 정책 변경" in q for q in result.rewritten_queries)
    # 원문 state.query 는 비파괴(불변).
    assert state.query == "그건 언제야?"


def test_state_query_is_not_mutated() -> None:
    state = _state()
    original_query = state.query
    manage_router(state, provider=_fake("operations_guide"))
    assert state.query == original_query


def test_langgraph_runnable_config_dict_is_ignored() -> None:
    """LangGraph 가 노드 시그니처의 ``config`` 키워드에 RunnableConfig dict 를 주입하는
    경로 시뮬레이션 — placeholder 가 흡수해 정상 분기로 진행해야 한다.

    feature12 회귀 보호: 이전 시그니처는 ``config`` 가 agent ``QueryRoutingConfig``
    였기 때문에 LangGraph 가 주입한 dict 가 normalize_routing_input 에 흘러 들어가
    RoutingProviderError 가 발생, OPERATION_GUIDE fallback 으로 떨어졌다. 본 테스트는
    fix 이후 dict 를 무시하고 fake provider 가 응답한 intent 로 정상 분기하는지 확인.
    """
    runnable_config_like = {"configurable": {"thread_id": "lg-1"}, "tags": []}
    result = manage_router(
        _state(),
        runnable_config_like,  # type: ignore[arg-type]
        provider=_fake("incident_response"),
    )
    assert result.intent is Intent.INCIDENT_RESPONSE


def test_routing_config_keyword_is_accepted() -> None:
    """``routing_config=`` keyword-only 인자가 agent config 로 정상 전달되는지 확인.

    feature12 회귀 보호: query_graph.py partial wiring 이 ``routing_config=deps.
    routing_config`` 로 갱신된 정합 확인. LLM 힌트가 제공된 경우 ``max_query_count``
    가 길이 상한으로 작용해 갯수가 그 이하로 잘리는 흐름을 검증한다 (default_query
    _count 는 LLM 힌트가 없을 때 deterministic fallback 이 채우는 갯수이므로
    명시 힌트가 있는 경우 max_query_count 가 진짜 상한).
    """
    from query_routing_agent.config import QueryRoutingConfig

    # agent validation 정합: default_query_count <= max_query_count 가 강제이므로
    # 둘을 함께 2 로 낮춰 max_query_count 가 길이 상한으로 작용하는 흐름을 검증한다.
    custom_config = QueryRoutingConfig(
        model="gpt-4o-mini",
        default_query_count=2,
        max_query_count=2,
    )
    hints = ["힌트1", "힌트2", "힌트3"]  # 3 개 힌트 제공 → max_query_count=2 로 잘려야 함.
    result = manage_router(
        _state(),
        provider=_fake("operations_guide", expanded_queries=hints),
        routing_config=custom_config,
    )
    assert result.intent is Intent.OPERATION_GUIDE
    # max_query_count=2 가 적용되면 rewritten_queries 갯수는 2 이하.
    assert len(result.rewritten_queries) <= 2
