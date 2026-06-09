"""대화 제목 생성기 — meta 이벤트 ``title`` 채움 [Storage].

--------------------------------------------------
작성자 : 최태성
작성목적 : api-spec v2.2.0 §1-1 meta 이벤트의 ``title`` (Required: N — "LLM이 생성한
          현재 대화 제목")을 채우기 위한 헬퍼. 운영 모드는 GPT-4o-mini(보조 모델,
          app/CLAUDE.md §5 라우팅 정책)로 질문/답변을 짧은 한국어 제목으로 요약한다.
          OpenAI 키/네트워크가 없는 PoC·테스트 환경에서는 LLM 호출 없이 질문 앞부분을
          잘라 결정론적 fallback 제목을 만든다. BFF 는 첫 assistant 응답 저장 시
          대화 제목이 기본값("새 대화")이면 본 ``title`` 로 1회 자동 설정한다(스펙 §1-1
          "대화 제목 자동 설정 규칙").
작성일 : 2026-05-29
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-29, 최초 작성, api-spec v2.2.0 정합 — meta.title 생성기(LLM + fallback).
--------------------------------------------------
[호환성]
  - Python 3.11.x, openai>=1.30 (운영 경로에서만 lazy import).
  - NOTE: 본 모듈은 [Storage] 분류 — 외부 LLM 호출 어댑터. 실패/타임아웃은 호출자
          (routes._resolve_title) 가 fallback 으로 흡수한다(책임 분리).
--------------------------------------------------
"""

# 제목 최대 길이(자) — fallback 절단 및 LLM 출력 방어 절단에 공통 적용.
_MAX_TITLE_LEN = 30

# 제목 생성 system 프롬프트 — 20자 이내 간결한 한국어 명사구, 장식 문자 없이 제목만.
_TITLE_SYSTEM_PROMPT = (
    "너는 사내 문서 기반 RAG 챗봇의 대화 제목 생성기다. "
    "사용자의 질문과 답변을 바탕으로 대화 주제를 한눈에 알 수 있는 간결한 한국어 제목을 만든다. "
    "규칙: 20자 이내, 명사구 형태, 따옴표·마침표·접두어('제목:' 등) 없이 제목 문구만 출력한다."
)


def _clean_title(raw: str) -> str:
    """LLM/입력 문자열을 제목으로 정리한다 — 개행 제거·따옴표 제거·길이 절단.

    LLM 이 따옴표로 감싸거나 여러 줄, 'title:' 접두어를 붙이는 경우를 방어한다.
    """
    text = " ".join(raw.split()).strip().strip("\"'“”‘’").strip()
    # 흔한 접두어 제거(대소문자 무관) — "제목:" / "title:".
    for prefix in ("제목:", "제목 :", "title:", "Title:"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix) :].strip()
    if len(text) > _MAX_TITLE_LEN:
        text = text[:_MAX_TITLE_LEN].rstrip()
    return text


def fallback_title(question: str) -> str:
    """LLM 없이 질문 앞부분으로 만드는 결정론적 fallback 제목.

    PoC·테스트(키/네트워크 없음)나 LLM 호출 실패 시 사용한다. 질문이 비어 있으면
    기본 제목("새 대화")을 반환해 meta.title 이 항상 비지 않도록 한다.

    Args:
        question: 사용자 질문 원문.

    Returns:
        최대 ``_MAX_TITLE_LEN`` 자로 절단한 제목. 빈 질문이면 "새 대화".
    """
    cleaned = _clean_title(question)
    return cleaned or "새 대화"


def generate_conversation_title(
    *,
    question: str,
    answer: str,
    api_key: str,
    model: str = "gpt-4o-mini",
    timeout_seconds: float = 10.0,
) -> str:
    """GPT-4o-mini 로 질문/답변을 짧은 한국어 대화 제목으로 요약한다(운영 경로).

    OpenAI Chat Completions 를 동기 호출한다. 호출/파싱 실패는 본 함수가 직접
    삼키지 않고 예외를 전파하며, 호출자(``routes._resolve_title``)가 ``fallback_title``
    로 흡수한다(책임 분리 — openai_transport 패턴 정합).

    Args:
        question: 사용자 질문 원문.
        answer: 생성된 답변 본문(제목 맥락 보강용).
        api_key: OpenAI API key (settings 에서 명시 주입).
        model: 사용할 모델. 기본 GPT-4o-mini(보조 모델).
        timeout_seconds: OpenAI 클라이언트 타임아웃(초).

    Returns:
        정리된 제목 문자열. LLM 이 빈 문자열을 반환하면 ``fallback_title`` 로 보정.

    Raises:
        Exception: OpenAI 호출 실패 등 — 호출자가 fallback 으로 처리한다.
    """
    # lazy import — openai 미설치(PoC) 환경에서도 모듈 import 가 깨지지 않게 한다.
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=timeout_seconds)
    completion = client.chat.completions.create(  # type: ignore[call-overload]
        model=model,
        messages=[
            {"role": "system", "content": _TITLE_SYSTEM_PROMPT},
            {"role": "user", "content": f"질문: {question}\n답변: {answer}"},
        ],
        temperature=0.3,
    )
    choices = getattr(completion, "choices", None) or []
    content = ""
    if choices:
        message = getattr(choices[0], "message", None)
        content = str(getattr(message, "content", "") or "")
    return _clean_title(content) or fallback_title(question)


__all__ = ["fallback_title", "generate_conversation_title"]
