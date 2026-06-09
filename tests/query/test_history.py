"""멀티턴 히스토리 관리자 어댑터 검증 (feature8 통합) — history-manager-agent ↔ RagState.

manage_history: vendoring한 history-manager-agent 로직을 in-process로 호출해 히스토리를
판단하고 RagState.history_decision에 채운다. agent LLM 호출은 FakeHistoryLLMProvider로 대체.
"""

from app.query.history import manage_history
from app.schemas.rag_state import HistoryDecision, HistoryTurn, RagState
from history_manager_agent.llm import FakeHistoryLLMProvider


def _state(
    *,
    conversation_id: str | None = "conv-1",
    history: list[HistoryTurn] | None = None,
    query: str = "그럼 롤백 절차는?",
) -> RagState:
    return RagState(
        query=query,
        user_id="user-1",
        conversation_id=conversation_id,
        history=history if history is not None else [],
    )


def _history() -> list[HistoryTurn]:
    return [
        HistoryTurn(role="user", content="IAM 정책 변경 중 장애가 발생했어."),
        HistoryTurn(role="assistant", content="IAM 정책 장애는 영향 범위 확인 후 롤백합니다."),
    ]


def _fake(decision: str, confidence: float = 0.9) -> FakeHistoryLLMProvider:
    return FakeHistoryLLMProvider(
        {"history_decision": decision, "confidence": confidence, "reason": f"{decision} 판정"}
    )


def test_no_conversation_id_shortcuts_to_new_topic() -> None:
    # conversation_id가 없으면 agent 호출 없이 new_topic으로 단축 처리
    result = manage_history(_state(conversation_id=None, history=_history()))
    assert isinstance(result.history_decision, HistoryDecision)
    assert result.history_decision.decision == "new_topic"
    assert result.history_decision.contextualized_question == "그럼 롤백 절차는?"
    assert result.history_decision.reset_required is True


def test_empty_history_is_new_topic() -> None:
    # conversation_id는 있으나 history가 비면 LLM 호출 없이 new_topic
    result = manage_history(_state(conversation_id="conv-1", history=[]))
    assert result.history_decision.decision == "new_topic"
    assert result.history_decision.contextualized_question == "그럼 롤백 절차는?"


def test_follow_up_classification_preserves_context() -> None:
    state = _state(history=_history())
    result = manage_history(state, provider=_fake("follow_up"))
    decision = result.history_decision
    assert decision.decision == "follow_up"
    assert decision.reset_required is False
    # follow_up은 직전 turn들을 preserved_context로 보존한다
    assert decision.preserved_context["turn_refs"] == ["turn-0", "turn-1"]
    # 재작성된 contextualized question에 원문 질문이 포함된다
    assert state.query in decision.contextualized_question


def test_new_topic_classification_resets() -> None:
    state = _state(history=_history())
    decision = manage_history(state, provider=_fake("new_topic")).history_decision
    assert decision.decision == "new_topic"
    assert decision.reset_required is True
    assert decision.contextualized_question == state.query


def test_ambiguous_classification_is_conservative() -> None:
    state = _state(history=_history())
    decision = manage_history(state, provider=_fake("ambiguous", confidence=0.3)).history_decision
    assert decision.decision == "ambiguous"
    assert decision.reset_required is False
    assert decision.confidence == 0.3
    # ambiguous는 과도한 추론 없이 원문 질문을 유지한다
    assert decision.contextualized_question == state.query


def test_query_and_needs_search_not_mutated() -> None:
    state = _state(history=_history())
    manage_history(state, provider=_fake("follow_up"))
    # query는 원문 비파괴, needs_search는 기본값 유지 (agent MVP는 검색스킵 신호를 내지 않음)
    assert state.query == "그럼 롤백 절차는?"
    assert state.needs_search is True


def test_returns_ragstate_with_history_decision() -> None:
    result = manage_history(_state(history=_history()), provider=_fake("follow_up"))
    assert isinstance(result, RagState)
    assert isinstance(result.history_decision, HistoryDecision)


def test_history_turns_converted_and_passed_to_provider() -> None:
    # RagState.history(HistoryTurn)가 agent 입력(ConversationTurn)으로 변환돼 provider에 전달된다
    fake = _fake("follow_up")
    manage_history(_state(history=_history()), provider=fake)
    assert len(fake.requests) == 1
    assert len(fake.requests[0].history_context) == 2
