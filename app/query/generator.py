"""답변 생성기 — answer-generation-agent 통합 어댑터 노드 [Agent].

--------------------------------------------------
작성자 : 최태성
작성목적 : Agent 담당자가 전달한 Answer Generation Agent(vendoring한
          ``answer_generation_agent`` 패키지)를 RAG Query 파이프라인의 답변 생성
          단계에 통합한다. 파일 기반 CLI workflow 대신 패키지의 조립 가능한 로직
          함수(normalize → prompt → generate → citation mapping → output build)를
          in-process로 호출해, RagState ↔ agent 스키마를 잇는 어댑터 노드를
          제공한다 (rag-pipeline-design.md §4.6, ai-agent/answer-generation-agent/
          answer-generation-agent.md).
작성일 : 2026-05-19
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-19, 최초 작성, Agent 통합 2/4 — manage_generator 어댑터 노드
  - 2026-05-19, Mode B 시연 fix — LangGraph 가 노드 시그니처의 ``config`` 키워드
    를 자체 ``RunnableConfig`` (dict) 로 자동 주입하는 충돌 발견. ``config`` 인자
    를 placeholder 로 유지하고 agent ``AnswerGenerationConfig`` 는 ``generation
    _config`` keyword-only 인자로 분리. 외부 wiring (``query_graph.py`` partial)
    도 ``generation_config=`` 로 갱신.
  - 2026-05-19, feature15 Rate Limit fallback — 설계서 §4.6.5 정합. agent
    ``AnswerProviderError(error_type='rate_limit_error')`` 캐치 후
    ``AnswerGenerationService.generate(use_fallback_model=True)`` 로 1회 재시도.
    ``state.used_llm`` 은 generation_result.model 로부터 정합 — fallback 시
    GPT-4o-mini, 정상 시 GPT-4o. 재시도 시 logging.warning 으로 운영 로그 기록
    (다음 세션 feature17 의 ``llm_fallback_total`` 카운터로 후속 가시화). 다른
    에러 (timeout/server/auth/invalid_response) 는 기존 ``_apply_fallback`` 유지.
  - 2026-05-19, feature17a LLM 커스텀 메트릭 — ``llm_fallback_total`` (Rate
    Limit fallback 발생 시 inc) + ``answer_generation_latency_seconds`` (답변
    생성 단계 latency observe). 설계서 §6.4 KPI 환각 비율 / P95 관측 지점
    정합. logging.warning 은 그대로 보존 (운영 로그 + 메트릭 이중화).
  - 2026-06-04, 문서 정합 — (A)SSE 토큰 스트리밍 / (B)운영 OpenAI HTTP transport /
    (C)Rate Limit fallback 이 이후 세션에 구현 완료됨을 반영(하단 "구현 현황").
    (D)(E)는 agent 패키지 담당으로 이관 유지.
--------------------------------------------------
[호환성]
  - Python 3.11.x, Pydantic 2.7+
  - NOTE: answer_generation_agent 는 ai-agent 저장소에서 vendoring 한 별도 패키지
          이며 무수정 보존한다. 본 어댑터만 RAG 컨벤션을 따른다. LLM provider
          기본값(provider=None)은 FakeAnswerLLMProvider (PoC·테스트)이며, 운영
          (build_real_deps)은 OpenAIAnswerLLMProvider 에 app/query/openai_transport.py
          의 build_openai_chat_transport 를 주입해 GPT-4o 를 직접 호출한다.

[구현 현황 — rag-pipeline-design.md §4.6 정합]
  - (A) SSE 토큰 스트리밍 (설계서 §4.6.4) — ✅ 구현(feature14). 운영 경로는
        app/query/openai_streaming.py 의 stream_openai_answer 로 token chunk 를
        다중 송신한다. PoC(OpenAI 키/generator_provider 없음)는 전체 답변 1회
        송신으로 자동 fallback.
  - (B) 운영 OpenAI HTTP transport (설계서 §4.6.3 GPT-4o 운영 호출) — ✅ 구현.
        build_real_deps 가 OpenAIAnswerLLMProvider 에 app/query/openai_transport.py
        의 build_openai_chat_transport(동기 HTTP transport)를 주입한다.
  - (C) Rate Limit Fallback — GPT-4o-mini 다운그레이드 (설계서 §4.6.5) — ✅ 구현
        (feature15). AnswerProviderError(error_type='rate_limit_error') 캐치 후
        generate(use_fallback_model=True) 로 1회 재시도하고 llm_fallback_total
        메트릭을 inc 한다.
  - (D) Function Calling 스키마 강제 (설계서 §4.6.1) — 이관(Agent 담당자 영역).
        agent 는 prompt instruction 으로 JSON schema 요청, OpenAI tools= 미설정.
        본 저장소가 수정하지 않음.
  - (E) 자연어 출처 인용 패턴 — "[스페이스명]…" / "첨부 파일 [filename]에 따르면…"
        (설계서 §4.6.1 v0.2.2 신설) — 이관(Agent 담당자 영역). agent prompt
        template 에 미반영. 본 저장소가 수정하지 않음.
--------------------------------------------------
"""

import hashlib
import logging
import time
from typing import Any

from answer_generation_agent.config import AnswerGenerationConfig
from answer_generation_agent.generation.answer_generation import (
    AnswerGenerationService,
    AnswerLLMProvider,
    AnswerProviderError,
    FakeAnswerLLMProvider,
)
from answer_generation_agent.generation.answer_output_builder import (
    build_answer_output,
)
from answer_generation_agent.generation.citation_mapping import map_citations
from answer_generation_agent.generation.input_normalization import (
    normalize_generation_input,
)
from answer_generation_agent.schemas import (
    AnswerOutput,
    AnswerStatus,
    GeneratedSource,
    TaskPromptType,
)
from app.metrics import answer_generation_latency_seconds, llm_fallback_total
from app.schemas.chunk import Chunk
from app.schemas.enums import Intent, LlmModel, SourceType
from app.schemas.rag_state import RagState
from app.schemas.response import Source

_LOGGER = logging.getLogger(__name__)

# Rate Limit fallback 결과로 사용되는 GPT-4o-mini 의 LlmModel enum 정합 매핑 키.
# generation_result.model 문자열에 ``mini`` 가 포함되면 GPT_4O_MINI 로 매핑한다
# (settings.llm_aux_model 기본값이 ``gpt-4o-mini`` 이므로 정합). 매핑 실패 시
# state.target_llm fallback 으로 보존 — 응답 meta 가 빈 채 떨어지지 않도록 보호.
_FALLBACK_MODEL_TOKEN = "mini"

# Intent (rag, 한국어) → TaskPromptType (agent, 영어) 매핑 표. rag-pipeline-design.md
# §4.6.2 / §6.6 표와 정합: 장애 대응 → timeline / 운영 가이드 → step_by_step /
# 정책·절차 → evidence_first / 이력 조회 → history_summary. Intent 가 None 이거나
# 매핑되지 않으면 GENERAL 로 fallback (agent normalization 정합).
_INTENT_TO_TASK_PROMPT: dict[Intent, TaskPromptType] = {
    Intent.INCIDENT_RESPONSE: TaskPromptType.TIMELINE,
    Intent.OPERATION_GUIDE: TaskPromptType.STEP_BY_STEP,
    Intent.POLICY_PROCEDURE: TaskPromptType.EVIDENCE_FIRST,
    Intent.HISTORY_LOOKUP: TaskPromptType.HISTORY_SUMMARY,
}

# Intent (rag, 한국어) → agent IntentLabel (영어 snake_case) 매핑. query-routing-agent
# 와 정합 (router.py 의 역방향). Intent 가 None 이면 UNKNOWN 으로 fallback.
_INTENT_TO_AGENT_LABEL: dict[Intent, str] = {
    Intent.INCIDENT_RESPONSE: "incident_response",
    Intent.OPERATION_GUIDE: "operations_guide",
    Intent.POLICY_PROCEDURE: "policy_procedure",
    Intent.HISTORY_LOOKUP: "history_lookup",
}

# PoC fake provider 가 응답할 기본 답변 schema — agent parse_llm_response 정합.
# answer 가 있어야 AnswerLLMResult 가 생성된다. sentences[*].citations 는 빈 배열
# 이라도 citation_mapping 의 _fallback_citations 가 단일 context 인 경우 자동 채움.
_DEFAULT_FAKE_RESPONSE: dict[str, Any] = {
    "answer": "Fake answer generated from provided context.",
    "sentences": [
        {
            "text": "Fake answer generated from provided context.",
            "citations": [],
        }
    ],
    "unsupported_gaps": [],
}


def manage_generator(
    state: RagState,
    config: Any = None,  # noqa: ARG001 — LangGraph 가 RunnableConfig dict 를 주입할 자리. 무시.
    *,
    provider: AnswerLLMProvider | None = None,
    generation_config: AnswerGenerationConfig | None = None,
) -> RagState:
    """답변 생성기 노드 — top_chunks 로 답변·sources·used_llm 을 채운다.

    vendoring 한 answer-generation-agent 의 in-process 로직 함수(normalize → prompt
    → generate → citation mapping → output build)를 호출한다. agent 의 sentence-
    level citation 출력은 답변 텍스트의 ``[#N]`` 인용 마커로 합성된다
    (rag-pipeline-design.md §4.6.1 — "모든 문장에 근거 청크 번호를 [#1], [#2] 형식
    으로 명시"). 이 마커는 검증 1단계(``verify_answer_rules``)의 입력이 된다.

    설계서 §4.6.3 정합으로 ``used_llm`` 은 라우터가 결정한 ``target_llm`` 을 우선
    사용하고, 없으면 GPT_4O 기본. 의도별 task prompt 는 Intent (한국어) →
    TaskPromptType (영어 snake_case) 매핑으로 전달한다 (§4.6.2 / §6.6).

    Args:
        state: Query 파이프라인 상태. ``query`` / ``user_id`` / ``conversation_id`` /
            ``intent`` / ``rewritten_queries`` / ``top_chunks`` / ``target_llm`` /
            ``history_decision`` 을 읽는다.
        config: LangGraph 가 노드에 자동 주입하는 ``RunnableConfig`` (dict). 본
            어댑터는 사용하지 않으며 무시한다 — keyword name 충돌 회피 목적의
            placeholder. agent 의 ``AnswerGenerationConfig`` 는 ``generation_config``
            인자로 받는다 (LangGraph 가 ``config`` 키워드를 자체 RunnableConfig 로
            override 하기 때문에 분리).
        provider: 답변 생성 LLM provider. None 이면 FakeAnswerLLMProvider 를 쓴다
            (PoC·테스트). 운영은 OpenAIAnswerLLMProvider + ``build_openai_chat_
            transport`` 주입 — ``build_real_deps`` 가 wiring.
        generation_config: 답변 생성 실행 설정. None 이면 기본값 (model=
            "configurable", temperature=0.2, timeout_seconds=45, max_contexts=5,
            max_answer_sentences=8) 사용.

    Returns:
        ``answer`` / ``sources`` / ``used_llm`` 가 채워진 RagState. ``[#N]`` 인용
        마커 포함. 다른 필드(상류 노드 산출물)는 보존.
    """
    # 검색 0건 분기는 그래프 차원에서 처리 (after_search_branch). 본 노드에 도달했을
    # 때 top_chunks 가 비어 있는 경로는 정상 흐름에선 없지만, 방어 처리로 stub 정합
    # 빈 답변을 반환한다.
    if not state.top_chunks:
        state.answer = ""
        state.used_llm = state.target_llm or LlmModel.GPT_4O
        return state

    selected_config = generation_config or AnswerGenerationConfig()
    selected_provider = provider or FakeAnswerLLMProvider(response=_DEFAULT_FAKE_RESPONSE)

    payload = _build_generation_input_payload(state)

    # feature17a — 답변 생성 단계만의 latency 를 별도 histogram 으로 관측 (HTTP latency
    # 와 분리). normalize → generate → citation_mapping → output_build 전체를 포함.
    started = time.perf_counter()
    try:
        normalized = normalize_generation_input(
            payload,
            max_contexts=selected_config.max_contexts,
        )
        service = AnswerGenerationService(provider=selected_provider)
        generation_result = _generate_with_rate_limit_fallback(
            service=service,
            normalized_input=normalized,
            config=selected_config,
        )
        citation_result = map_citations(
            generation_result=generation_result,
            normalized_input=normalized,
        )
        answer_output = build_answer_output(
            normalized_input=normalized,
            generation_result=generation_result,
            citation_result=citation_result,
        )
    except Exception:  # noqa: BLE001 — agent provider/parsing 실패는 안전 fallback 으로 흡수.
        answer_generation_latency_seconds.observe(time.perf_counter() - started)
        return _apply_fallback(state)
    answer_generation_latency_seconds.observe(time.perf_counter() - started)

    state.answer = _compose_answer_with_citations(answer_output)
    state.sources = _agent_sources_to_rag_sources(
        agent_sources=answer_output.sources,
        top_chunks=state.top_chunks,
    )
    # 사용된 모델을 generation_result.model 에서 정합 — Rate Limit fallback 시 GPT-4o-
    # mini 로 다운그레이드된 사실이 응답 meta.used_llm 으로 사용자에게 노출된다.
    state.used_llm = _resolve_used_llm(generation_result.model, state.target_llm)
    return state


def _generate_with_rate_limit_fallback(
    *,
    service: AnswerGenerationService,
    normalized_input: Any,
    config: AnswerGenerationConfig,
) -> Any:
    """설계서 §4.6.5 Rate Limit fallback — 429 시 fallback_model 로 1회 재시도.

    agent ``AnswerProviderError(error_type='rate_limit_error')`` 만 다운그레이드
    트리거. 다른 error_type (timeout/auth/invalid_response/server) 은 상위로 그대로
    raise 해 ``_apply_fallback`` 안전 분기로 이어지게 한다 (운영 시점에 다운그레이드
    가 의미 없는 에러까지 GPT-4o-mini 로 강제 시도하지 않도록 보호).

    재시도 시점에 ``logging.warning`` 으로 운영 로그 기록 — 다음 세션 feature17
    의 ``llm_fallback_total`` Prometheus 카운터로 후속 가시화 (현재는 logging 만).
    """
    try:
        return service.generate(normalized_input=normalized_input, config=config)
    except AnswerProviderError as exc:
        if exc.error_type != "rate_limit_error":
            raise
        _LOGGER.warning(
            "answer generator rate-limited, falling back to fallback_model=%s",
            config.fallback_model,
        )
        # feature17a — Prometheus 카운터로 Rate Limit fallback 빈도 가시화.
        llm_fallback_total.labels(
            from_model=config.model,
            to_model=config.fallback_model,
            reason="rate_limit_error",
        ).inc()
        return service.generate(
            normalized_input=normalized_input,
            config=config,
            use_fallback_model=True,
        )


def _resolve_used_llm(model: str, target_llm: LlmModel | None) -> LlmModel:
    """generation_result.model 문자열을 LlmModel enum 으로 안전 매핑.

    - 정확 매칭 (``gpt-4o`` / ``gpt-4o-mini``) 우선
    - 문자열에 ``mini`` 포함 → GPT_4O_MINI (Rate Limit fallback 결과)
    - 매핑 실패 → target_llm 보존 (라우터가 결정한 값), 없으면 GPT_4O.
    """
    try:
        return LlmModel(model)
    except ValueError:
        if _FALLBACK_MODEL_TOKEN in model.lower():
            return LlmModel.GPT_4O_MINI
        return target_llm or LlmModel.GPT_4O


def _build_generation_input_payload(state: RagState) -> dict[str, Any]:
    """RagState 를 answer-generation-agent 의 GenerationInput dict 로 변환한다.

    conversation_id 가 없으면 결정론 합성값을 채워 normalization 의 required 체크를
    통과시킨다 — SSE 라우트에서 conversation_id 가 None 으로 들어오는 경로 (싱글턴)
    를 안전 fallback 없이 흡수하기 위함.
    """
    intent = state.intent
    if intent is None:
        task_prompt_type = TaskPromptType.GENERAL.value
        intent_label = "unknown"
    else:
        task_prompt_type = _INTENT_TO_TASK_PROMPT.get(intent, TaskPromptType.GENERAL).value
        intent_label = _INTENT_TO_AGENT_LABEL.get(intent, "unknown")
    conversation_id = state.conversation_id or _synthesize_conversation_id(state)
    routing_id = _synthesize_routing_id(conversation_id, state.query, intent_label)
    expanded_queries = list(state.rewritten_queries) or [state.query]
    original_question = state.query
    contextualized_query = (
        state.history_decision.contextualized_question
        if state.history_decision is not None and state.history_decision.contextualized_question
        else state.query
    )
    confidence = (
        float(state.history_decision.confidence) if state.history_decision is not None else 0.0
    )
    metadata_filters = dict(state.metadata_filters) if state.metadata_filters else {}
    pool_weights = dict(state.pool_weights) if state.pool_weights else {}

    return {
        "conversation_id": conversation_id,
        "user_id": state.user_id,
        "routing_decision": {
            "routing_id": routing_id,
            "original_question": original_question,
            "query": contextualized_query,
            "intent": intent_label,
            "task_prompt_type": task_prompt_type,
            "expanded_queries": expanded_queries,
            "metadata_filters": metadata_filters,
            "pool_weights": pool_weights,
            "confidence": confidence,
            "warnings": [],
        },
        "search_results": {
            "top_contexts": [
                _chunk_to_top_context_payload(
                    chunk,
                    index=index,
                    rerank_score=state.rerank_scores.get(chunk.metadata.chunk_id),
                )
                for index, chunk in enumerate(state.top_chunks, start=1)
            ],
        },
        "metadata": {"locale": "ko-KR", "timezone": "Asia/Seoul"},
    }


def _chunk_to_top_context_payload(
    chunk: Chunk, *, index: int, rerank_score: float | None = None
) -> dict[str, Any]:
    """RAG Chunk 를 agent TopContext dict 로 변환한다.

    context_id 는 chunk_id 의 짧은 별칭("ctx-{index:03d}-{chunk_id[:8]}")으로 합성해
    검증 1단계의 ``[#N]`` 마커 N(1-based 순번)과 의미상 연결한다.

    feature17c-3 (2026-05-20): ``rerank_score`` 가 주어지면(rerank_node 가 RagState.
    rerank_scores 에 저장한 실제 Cross-Encoder 점수) 그것을 그대로 전달한다. agent 는
    이 값을 GeneratedSource.rerank_score 로 보존하고(citation_mapping), generator 의
    ``_agent_sources_to_rag_sources`` 가 ``rerank_score × 100`` 으로 Source.score 를
    산출하므로, 실제 rerank 점수가 출처 카드 점수까지 도달한다.

    ``rerank_score`` 가 None(rerank_scores 미제공·legacy 경로)이면, agent 의 _sort_key
    (rerank_score → score → 입력 순)가 rag rerank Top-K 순서를 보존하도록 ``1 - 0.001*
    index`` fallback 값을 부여한다(기존 동작).
    """
    metadata = chunk.metadata
    context_id = f"ctx-{index:03d}-{metadata.chunk_id[:8]}"
    effective_rerank_score = rerank_score if rerank_score is not None else 1.0 - 0.001 * index
    return {
        "context_id": context_id,
        "document_id": metadata.page_id,
        "chunk_id": metadata.chunk_id,
        "title": metadata.page_title or metadata.attachment_filename or context_id,
        "space_key": metadata.space_key or "UNKNOWN",
        "source_url": metadata.webui_link or f"chunk://{metadata.chunk_id}",
        "content": chunk.text,
        "score": 0.0,
        "rerank_score": effective_rerank_score,
        "metadata": {
            "page_id": metadata.page_id,
            "attachment_filename": metadata.attachment_filename,
            "section_header": metadata.section_header,
            "section_path": metadata.section_path,
            "source_type": metadata.source_type.value,
        },
    }


def _synthesize_conversation_id(state: RagState) -> str:
    """conversation_id 가 None 일 때 결정론 합성값을 만든다 (싱글턴 안전 fallback)."""
    seed = f"singleton|{state.user_id}|{state.query}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"conv-{digest}"


def _synthesize_routing_id(conversation_id: str, query: str, intent_label: str) -> str:
    """routing_id 는 (conversation_id, query, intent) 의 sha1 16자 결정론 식별자."""
    seed = f"{conversation_id}|{query}|{intent_label}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"routing-{digest}"


def _compose_answer_with_citations(answer_output: AnswerOutput) -> str:
    """agent AnswerOutput 을 검증 1단계가 인식할 ``[#N]`` 마커 포함 답변으로 재조립.

    rag-pipeline-design.md §4.6.1 "모든 문장에 근거 청크 번호를 [#1], [#2] 형식으로
    명시" 정합. context_id 는 ``used_context_ids`` 의 등장 순서(1-based)를 N 으로
    매핑한다 (agent answer_output_builder 의 _build_sources 와 동일 순서).

    insufficient_context / failed 상태는 agent answer 텍스트를 그대로 사용한다
    (사용자에게 보여줄 표준 안내문이 이미 채워져 있음). success 상태는 sentences 를
    "{text} [#N1][#N2]" 형식으로 조립한다. sentences 가 비어 있고 answer 만 있는
    경우(LLM 이 sentences 누락) 답변 끝에 ``[#1]`` 1회 부착 — 검증 1단계가 적어도
    동작하도록 보장.
    """
    if answer_output.answer_status != AnswerStatus.SUCCESS:
        return answer_output.answer

    context_id_to_number = {
        context_id: number
        for number, context_id in enumerate(answer_output.used_context_ids, start=1)
    }

    sentence_parts: list[str] = []
    for sentence in answer_output.sentences:
        sentence_text = sentence.text.rstrip()
        markers = _citations_to_markers(sentence.citations, context_id_to_number)
        if markers:
            sentence_parts.append(f"{sentence_text} {markers}")
        else:
            sentence_parts.append(sentence_text)

    if sentence_parts:
        return " ".join(sentence_parts)
    # sentences 가 비었지만 answer 가 있는 경우 — 검증 1단계 동작 보장용 [#1] 부착.
    return f"{answer_output.answer.rstrip()} [#1]"


def _citations_to_markers(
    citations: list[str],
    context_id_to_number: dict[str, int],
) -> str:
    """citation 배열을 ``[#N1][#N2]`` 형식 마커 문자열로 변환한다."""
    numbers: list[int] = []
    for citation in citations:
        number = context_id_to_number.get(citation)
        if number is not None and number not in numbers:
            numbers.append(number)
    return "".join(f"[#{number}]" for number in numbers)


def _agent_sources_to_rag_sources(
    *,
    agent_sources: list[GeneratedSource],
    top_chunks: list[Chunk],
) -> list[Source]:
    """agent GeneratedSource 를 rag Source 로 변환한다.

    Cross-Encoder rerank_score (0.0~1.0)는 본 어댑터가 부여한 폭에 가까운 값이므로
    UI 표시용 정수 점수 (0~100)는 chunk metadata 의 rerank 결과로 역산할 수 없다.
    chunk 매칭이 가능하면 chunk.metadata 의 정보 (attachment_* / download_url)를
    보존하고, 점수는 rerank_score(0~1) × 100 의 정수로 변환한다.
    """
    chunk_by_id = {chunk.metadata.chunk_id: chunk for chunk in top_chunks}
    sources: list[Source] = []
    for agent_source in agent_sources:
        chunk = chunk_by_id.get(agent_source.chunk_id)
        if chunk is None:
            # agent 가 fallback citation 으로 만들어낸 source 가 본문 top_chunks 에
            # 없는 비정상 경로 — 안전을 위해 본 source 는 건너뛰고 다음으로 진행.
            continue
        metadata = chunk.metadata
        source_type = SourceType(metadata.source_type.value)
        score_int = max(0, min(100, int(round(agent_source.rerank_score * 100))))
        sources.append(
            Source(
                title=metadata.page_title,
                score=score_int,
                path=metadata.section_path or metadata.page_title,
                space_key=metadata.space_key,
                source_type=source_type,
                confluence_url=metadata.webui_link,
                last_modified=metadata.last_modified,
                text_preview=_preview_text(chunk.text),
                attachment_filename=metadata.attachment_filename,
                attachment_mime=metadata.attachment_mime,
                download_url=None,
            )
        )
    return sources


def _preview_text(text: str, *, max_chars: int = 200) -> str:
    """text_preview — 첫 max_chars 자만 자른 안전 fallback (rag Source.text_preview)."""
    clean = text.strip()
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars]


def _apply_fallback(state: RagState) -> RagState:
    """provider 실패·정규화 실패 시 stub 정합 안전 fallback.

    rag-pipeline-design.md §4.6.5 "Function Calling 출력 스키마 위반 → plain text
    응답으로 폴백, 출처 매핑은 검증기 1단계 결과로 대체" 정합. stub 의 [#1] 답변
    포맷과 동일하게 top_chunks[0] 기반 안내문을 채워 검증 1단계가 적어도 동작
    하도록 한다.
    """
    state.used_llm = state.target_llm or LlmModel.GPT_4O
    if not state.top_chunks:
        state.answer = ""
        return state
    top = state.top_chunks[0]
    title = top.metadata.attachment_filename or top.metadata.page_title
    state.answer = f"[#1] {title} 관련 정보를 다음과 같이 안내합니다."
    return state


__all__ = ["manage_generator"]
