"""답변 생성기 — OpenAI Chat Completions transport (non-streaming) [Storage].

--------------------------------------------------
작성자 : 최태성
작성목적 : answer-generation-agent 의 ``OpenAIAnswerLLMProvider`` 는 transport
          callable (``Callable[[dict], dict]``) 주입을 요구한다. 본 모듈은
          ``openai>=1.30`` 클라이언트로 Chat Completions API 를 호출하고 결과를
          agent ``parse_llm_response`` 가 기대하는 JSON dict (``{answer, sentences,
          unsupported_gaps}``)로 반환하는 transport callable 을 제공한다
          (rag-pipeline-design.md §4.6.3 GPT-4o 운영 호출).
작성일 : 2026-05-19
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-19, 최초 작성, (B) 운영 OpenAI HTTP transport — OpenAIAnswerLLM
    Provider 의 transport 자리 wiring. JSON 강제 (response_format=json_object)
    + developer role 합산.
  - 2026-05-20, feature17c-14 환각 보수성 guard (opt-in) — 생성기 시스템 프롬프트
    는 vendoring(``answer_generation_agent``) 안에 하드코딩돼 외부 주입 seam 이
    없다. transport 가 system/developer 메시지를 단일 system 으로 합치는 본
    어댑터 경계에서 ``system_prompt_suffix`` 를 마지막 system part 로 덧붙여,
    vendoring 무수정으로 보수성(context 밖 단정 금지·미근거 문장 억제)을 강화한다.
    기본 None(기존 동작 무변). ``build_real_deps`` 가 ``settings.generator_
    conservative_guard`` True 일 때만 ``CONSERVATIVE_SYSTEM_GUARD`` 를 주입한다.
  - 2026-05-22, feature17c-25 문장별 인용 구조 강제 (opt-in) — 환각 KPI(15%) 미달의
    유일 잔여 원인이 "다중 청크 종합 문장의 단일 인용"(citation 정밀도)임이 진단으로
    확정됐고(요청서 §3, 12/12 flip), 프롬프트 텍스트 개입(17c-22/23)은 효과 0 으로
    실증 실패했다. Agent 담당자 권한 위임에 따라, vendored 프롬프트 대신 transport
    경계에서 OpenAI **Structured Outputs(json_schema, strict)** 를 주입해 출력 구조를
    강제한다(``GROUNDED_CITATION_RESPONSE_FORMAT``): sentences[].citations 를 문장마다
    필수 배열로 두고 다중 인용을 schema description 으로 유도. parse_llm_response 키
    (answer/sentences[].{text,citations}/unsupported_gaps)와 정합. 기본 OFF(json_object
    기존 동작) — ``settings.generator_force_citation_schema`` True 일 때만 주입, A/B 측정.
    한계: OpenAI strict json_schema 는 ``minItems`` 미지원이라 "≥1 인용"을 구조적으로
    강제하진 못한다(빈 배열 가능). 구조 보장 + description 으로 다중 인용을 유도하며,
    효과는 per-cited-chunk 환각 재평가로 확인한다.
--------------------------------------------------
[호환성]
  - Python 3.11.x, openai>=1.30 (pyproject.toml 의존성).
  - NOTE: 본 모듈은 [Storage] 분류 — 외부 API 호출 어댑터. agent 의 transport
          시그니처에 정합한 단순 callable 만 제공하고, retry/타임아웃/안전 fallback
          은 agent 본체와 호출자(manage_generator) 가 담당한다 (책임 분리).
--------------------------------------------------
"""

import json
from typing import Any

# agent transport callable 시그니처 — Callable[[dict], dict].
TransportPayload = dict[str, Any]
TransportResult = dict[str, Any]

# feature17c-14 — 환각 보수성 guard (opt-in). 생성기 시스템 프롬프트가 vendoring 안에
# 하드코딩돼 외부 주입 seam 이 없으므로, transport 가 메시지를 합치는 어댑터 경계에서
# system 메시지 끝에 덧붙이는 보강 지침. NOT_SUPPORTED(미근거 문장) 억제를 목표로 한다.
# vendored 기본 system_prompt("context 밖 사실 단정 금지" 등)를 약화하지 않고 강화만 한다.
# build_real_deps 가 settings.generator_conservative_guard=True 일 때만 주입(기본 OFF).
CONSERVATIVE_SYSTEM_GUARD = "\n".join(
    [
        "[보수성 강화 지침]",
        "- 각 문장은 인용한 context_id 의 내용에 명시적으로 등장하는 사실만 진술한다.",
        "- context 에 없는 추론·일반 지식·배경 설명·권고를 답변 문장으로 추가하지 않는다.",
        "- context 로 뒷받침되지 않는 내용은 답변 문장 대신 unsupported_gaps 에 기재한다.",
        "- 추측성 표현(아마도, ~일 수 있다, 일반적으로)을 사용하지 않는다.",
        "- 정확한 citation 을 달 수 없는 문장은 출력하지 않는다.",
    ]
)


# feature17c-25 — 문장별 인용 구조 강제용 OpenAI Structured Outputs 스키마(opt-in).
# parse_llm_response(answer_generation.py)가 읽는 키(answer / sentences[].{text,citations}
# / unsupported_gaps)와 정확히 정합한다. strict=True 로 출력 구조를 보장하고, citations 를
# 문장마다 필수 배열로 둔다(prose 이탈 차단). description 으로 다중 인용·미근거 문장 분리를
# 유도한다. NOTE: strict json_schema 는 minItems 미지원이라 "≥1 인용"을 구조적으로 강제하진
# 못한다 — 빈 배열도 형식상 valid. 그 경우 검증기가 미인용 문장을 의심 처리(사양 정합).
GROUNDED_CITATION_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "grounded_answer",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["answer", "sentences", "unsupported_gaps"],
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "사용자에게 보여줄 최종 답변 본문.",
                },
                "sentences": {
                    "type": "array",
                    "description": (
                        "답변을 구성하는 문장들. 각 문장은 근거가 된 context_id 를 인용한다."
                    ),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["text", "citations"],
                        "properties": {
                            "text": {"type": "string", "description": "문장 원문."},
                            "citations": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "이 문장이 근거한 모든 context_id. 여러 청크를 종합한 "
                                    "문장은 근거가 된 context_id 를 하나도 빠짐없이 모두 "
                                    "나열한다(예: 두 청크 종합 시 두 개). 어떤 context 로도 "
                                    "뒷받침되지 않는 문장은 citations 를 빈 배열로 두고 그 "
                                    "내용은 unsupported_gaps 로 옮긴다. 무관한 context 를 "
                                    "채워 넣지 않는다."
                                ),
                            },
                        },
                    },
                },
                "unsupported_gaps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "어떤 context 로도 확인할 수 없어 답변에서 제외한 제한 사항.",
                },
            },
        },
    },
}


def select_generator_response_format(force_citation_schema: bool) -> dict[str, Any] | None:
    """생성기 transport 에 줄 response_format 을 고른다 (feature17c-25 wiring 헬퍼).

    ``force_citation_schema`` 가 True 면 ``GROUNDED_CITATION_RESPONSE_FORMAT``(Structured
    Outputs)를, False(기본)면 None 을 반환한다. None 이면 ``build_openai_chat_transport``
    가 기존 ``{"type": "json_object"}`` 로 폴백해 동작이 바뀌지 않는다. deps wiring 에서
    settings 토글을 받아 호출하는 순수 함수라 단위 테스트가 쉽다.
    """
    return GROUNDED_CITATION_RESPONSE_FORMAT if force_citation_schema else None


def build_openai_chat_transport(
    *,
    api_key: str,
    response_format: dict[str, Any] | None = None,
    system_prompt_suffix: str | None = None,
) -> Any:
    """OpenAI Chat Completions transport callable 을 빌드한다.

    반환되는 callable 은 agent ``OpenAIAnswerLLMProvider`` 의 transport 인자에
    그대로 주입할 수 있다. agent 가 ``request.to_safe_dict()`` (sanitized payload)
    를 전달하면, 본 callable 이 OpenAI Chat Completions API 를 동기 호출해
    응답을 JSON dict 로 파싱·반환한다.

    Args:
        api_key: OpenAI API key. ``OPENAI_API_KEY`` 환경변수에서 외부 주입한 값.
        response_format: OpenAI response_format. None 이면 ``{"type": "json_object"}``
            로 JSON 강제 (agent ``parse_llm_response`` 가 JSON dict 를 기대).
        system_prompt_suffix: feature17c-14 — None(기본)이 아니면 합쳐진 system
            메시지 끝에 덧붙이는 보수성 강화 지침. vendored system_prompt 를 약화하지
            않고 보강만 한다. ``CONSERVATIVE_SYSTEM_GUARD`` 를 주입하는 용도.

    Returns:
        ``Callable[[dict], dict]`` — agent transport 정합. 호출 실패 시
        ``answer_generation_agent.generation.answer_generation.OpenAITransportError``
        를 raise 한다 (agent 가 retry 분기 결정).
    """
    selected_response_format = response_format or {"type": "json_object"}

    def _transport(payload: TransportPayload) -> TransportResult:
        # lazy import — openai 패키지가 없는 환경에서도 모듈 import 가 깨지지 않게.
        from openai import APIError, APIStatusError, APITimeoutError, OpenAI

        from answer_generation_agent.generation.answer_generation import OpenAITransportError

        client = OpenAI(api_key=api_key, timeout=float(payload["timeout_seconds"]))
        messages = _normalize_messages(
            payload.get("messages", []),
            system_suffix=system_prompt_suffix,
        )
        try:
            # OpenAI SDK 의 chat.completions.create 는 messages/response_format 을
            # 엄격한 TypedDict 로 받지만 본 어댑터는 agent 가 정규화한 dict 를 그대로
            # 전달한다 — call-overload 무시 (런타임은 정상 동작, agent contract 정합).
            completion = client.chat.completions.create(  # type: ignore[call-overload]
                model=str(payload["model"]),
                messages=messages,
                temperature=float(payload["temperature"]),
                response_format=selected_response_format,
            )
        except APITimeoutError as exc:
            raise OpenAITransportError(status_code=None, message=str(exc)) from exc
        except APIStatusError as exc:
            raise OpenAITransportError(
                status_code=int(exc.status_code) if exc.status_code else 500,
                message=str(exc),
            ) from exc
        except APIError as exc:
            raise OpenAITransportError(status_code=500, message=str(exc)) from exc

        content = _extract_first_message_content(completion)
        return _parse_json_payload(content)

    return _transport


def _normalize_messages(
    messages: list[dict[str, Any]],
    *,
    system_suffix: str | None = None,
) -> list[dict[str, str]]:
    """agent 의 ``messages`` 를 OpenAI Chat Completions 가 받는 형식으로 정규화한다.

    agent 는 ``system`` / ``developer`` / ``user`` 3 role 을 전달하지만 OpenAI Chat
    Completions 는 ``developer`` role 을 별도로 받지 않는다 (GPT-4o 계열).
    ``developer`` 메시지는 ``system`` 다음 line 으로 합쳐서 단일 system 메시지로
    전달한다 (역할은 시스템 지시문으로 합리적 등가).

    feature17c-14 — ``system_suffix`` 가 주어지면(None/빈 문자열이 아니면) 합쳐진
    system part 의 **마지막**에 덧붙인다. vendored system_prompt 를 약화하지 않고
    보강만 하기 위해 끝에 둔다. system 메시지가 하나도 없던 경우에도 suffix 만으로
    단일 system 메시지를 만든다.
    """
    system_parts: list[str] = []
    other: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        if role in {"system", "developer"}:
            if content.strip():
                system_parts.append(content)
        else:
            other.append({"role": role, "content": content})
    if system_suffix and system_suffix.strip():
        system_parts.append(system_suffix)
    normalized: list[dict[str, str]] = []
    if system_parts:
        normalized.append({"role": "system", "content": "\n\n".join(system_parts)})
    normalized.extend(other)
    return normalized


def _extract_first_message_content(completion: Any) -> str:
    """OpenAI completion 객체에서 첫 메시지의 content 텍스트를 추출한다."""
    if not getattr(completion, "choices", None):
        return ""
    first_choice = completion.choices[0]
    message = getattr(first_choice, "message", None)
    if message is None:
        return ""
    return str(getattr(message, "content", "") or "")


def _parse_json_payload(content: str) -> TransportResult:
    """OpenAI Chat Completions 의 message content (JSON 텍스트)를 dict 로 파싱한다.

    JSON 강제 (response_format=json_object) 가 적용돼 있어도 LLM 이 빈 문자열이나
    잘못된 JSON 을 반환할 가능성이 있으므로 ``OpenAITransportError`` 로 흡수해
    agent retry 분기에 위임한다.
    """
    from answer_generation_agent.generation.answer_generation import OpenAITransportError

    if not content.strip():
        raise OpenAITransportError(status_code=500, message="OpenAI returned empty content")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise OpenAITransportError(
            status_code=500,
            message=f"OpenAI returned invalid JSON: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise OpenAITransportError(
            status_code=500,
            message="OpenAI returned non-object JSON",
        )
    return parsed


__all__ = [
    "CONSERVATIVE_SYSTEM_GUARD",
    "GROUNDED_CITATION_RESPONSE_FORMAT",
    "build_openai_chat_transport",
    "select_generator_response_format",
]
