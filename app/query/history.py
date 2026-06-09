"""멀티턴 히스토리 관리자 — history-manager-agent 통합 어댑터 노드 [Agent].

--------------------------------------------------
작성자 : 최태성
작성목적 : Agent 담당자가 전달한 멀티턴 히스토리 관리자(vendoring한 history_manager_agent
          패키지)를 RAG Query 파이프라인에 통합한다. 파일 기반 워크플로 대신 패키지의
          조립 가능한 로직 함수를 in-process로 호출해, RagState ↔ agent 스키마를 잇는
          어댑터 노드를 제공한다 (rag-pipeline-design.md §6 4.3, docs/history-manager-agent.md).
작성일 : 2026-05-15
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-15, 최초 작성, feature8 통합 — manage_history 어댑터 노드
--------------------------------------------------
[호환성]
  - Python 3.11.x, Pydantic 2.7+
  - NOTE: history_manager_agent는 ai-agent 저장소에서 vendoring한 별도 패키지이며
          무수정 보존한다. 본 어댑터만 RAG 컨벤션을 따른다. LLM provider 기본값은
          FakeHistoryLLMProvider(PoC·테스트)이며, 실제 OpenAIHistoryLLMProvider를 주입할 수 있다.
--------------------------------------------------
"""

from app.schemas.rag_state import HistoryDecision, RagState
from history_manager_agent.config import HistoryManagerConfig
from history_manager_agent.context import apply_context_policy
from history_manager_agent.history import normalize_history_input_payload
from history_manager_agent.llm import (
    FakeHistoryLLMProvider,
    HistoryClassification,
    HistoryLLMProvider,
    classify_history,
)
from history_manager_agent.question import build_question_result
from history_manager_agent.schemas import HistoryDecisionLabel

# history-manager-agent 워크플로의 기본 fake provider와 동일 — 실제 LLM 미설정 시의
# 보수적 기본값(new_topic). 대화 turn이 있을 때만 사용된다.
_DEFAULT_FAKE_CLASSIFICATION = {
    "history_decision": HistoryDecisionLabel.NEW_TOPIC.value,
    "confidence": 1.0,
    "reason": "Default fake provider classification (no LLM configured).",
}


def manage_history(
    state: RagState,
    *,
    provider: HistoryLLMProvider | None = None,
) -> RagState:
    """멀티턴 히스토리 관리자 노드 — 히스토리를 판단해 RagState.history_decision을 채운다.

    vendoring한 history-manager-agent의 로직 함수(정규화 → 분류 → 컨텍스트 정책 →
    contextualized question)를 in-process로 호출한다. 그 출력(follow_up/new_topic/
    ambiguous 판단, contextualized question, preserved context 등)을 RagState의
    `history_decision` 필드에 담는다. `query`는 원문 그대로 두어 비파괴적이며,
    `needs_search`는 agent MVP가 검색스킵 신호를 내지 않으므로 기본값을 유지한다.

    Args:
        state: Query 파이프라인 상태. `query`/`user_id`/`conversation_id`/`history`를 읽는다.
        provider: 히스토리 분류용 LLM provider. None이면 FakeHistoryLLMProvider를 쓴다
            (PoC·테스트). 실제 운영에서는 OpenAIHistoryLLMProvider를 주입한다.

    Returns:
        `history_decision`이 채워진 RagState (입력 state를 갱신해 반환).
    """
    # conversation_id가 없으면 대화 컨텍스트가 없으므로 agent 호출 없이 new_topic으로 처리한다.
    if not state.conversation_id:
        state.history_decision = HistoryDecision(
            decision=HistoryDecisionLabel.NEW_TOPIC.value,
            contextualized_question=state.query,
            reset_required=True,
            confidence=1.0,
            reason="conversation_id가 없어 새 주제로 처리합니다.",
        )
        return state

    config = HistoryManagerConfig()
    selected_provider = provider or FakeHistoryLLMProvider(_DEFAULT_FAKE_CLASSIFICATION)

    # RagState.history(HistoryTurn)를 agent 입력 payload로 변환한다. RagState의 HistoryTurn은
    # turn_id·created_at이 없으므로 turn_id는 순번으로 합성하고, created_at은 agent의
    # 결정론적 fallback에 맡긴다(목록 순서가 곧 시간 순서).
    payload = {
        "conversation_id": state.conversation_id,
        "user_id": state.user_id,
        "current_question": state.query,
        "history": [
            {"turn_id": f"turn-{index}", "role": turn.role, "content": turn.content}
            for index, turn in enumerate(state.history)
        ],
        "metadata": {},
    }
    normalized = normalize_history_input_payload(payload, config)

    # 빈 history는 LLM 호출 없이 new_topic으로 처리한다 (history-manager-agent 워크플로와 동일).
    if normalized.used_turn_count == 0:
        classification = HistoryClassification(
            history_decision=HistoryDecisionLabel.NEW_TOPIC,
            confidence=1.0,
            reason="Empty history is treated as a new topic.",
        )
    else:
        classification = classify_history(normalized, config, selected_provider)

    policy = apply_context_policy(normalized, classification)
    question_result = build_question_result(
        conversation_id=normalized.history_input.conversation_id,
        user_id=normalized.history_input.user_id,
        current_question=normalized.history_input.current_question,
        policy_result=policy,
        metadata=normalized.history_input.metadata,
    )

    preserved = question_result.preserved_context
    state.history_decision = HistoryDecision(
        decision=question_result.history_decision.value,
        contextualized_question=question_result.contextualized_question,
        preserved_context={
            "summary": preserved.summary,
            "entities": list(preserved.entities),
            "turn_refs": list(preserved.turn_refs),
        },
        reset_required=question_result.reset_required,
        confidence=question_result.confidence,
        reason=question_result.reason,
        warnings=list(question_result.warnings),
    )
    return state
