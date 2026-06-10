"""Error Response 정의 — api-spec.md Error Response 표 정합 [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : api-spec.md Error Response 표의 표준 코드(``ErrorCode``)를 정의한다.
          본 앱의 오류 표면은 SSE ``error`` 이벤트(routes._error_event — ``errorCode``/
          ``message``)뿐이며 실사용 값은 ML_* 3종이다. `RETRIEVAL_EMPTY` /
          `LOW_CONFIDENCE` / `VERIFICATION_BLOCKED` 같은 "표준 분기 응답"은 200 SSE
          성공 응답 내부에서 처리되고(`feedback_enabled=False` / 답변 대체), 나머지
          코드는 spec 표 호환으로 정의만 유지한다(HTTP 에러 봉투는 BFF 책임 — 하단 NOTE).
작성일 : 2026-05-18
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-18, 최초 작성, feature11 통합 Phase 2 — ErrorCode StrEnum +
    ErrorDetail / ErrorResponse Pydantic 모델 + HTTP status 매핑
  - 2026-05-29, api-spec v2.2.0 정합 — ``ErrorResponse`` 를 공통 Wrapper 의 4필드 봉투
    (``isSuccess`` / ``code`` / ``errorCode`` / ``message``)로 재정의(구 ``success`` +
    중첩 ``error`` 형태 제거). 중첩 ``ErrorDetail`` 모델 삭제, ``error_response`` 헬퍼가
    HTTP ``code`` 를 ``HTTP_STATUS_BY_CODE`` 에서 도출하도록 변경.
  - 2026-06-10, 코드 리뷰 재점검(P4) — 미사용 HTTP 에러 머신(``ErrorResponse`` /
    ``HTTP_STATUS_BY_CODE`` / ``error_response``) 삭제. 본 앱의 오류 표면은 SSE
    ``error`` 이벤트(routes._error_event)뿐이며 4필드 봉투 직렬화는 BFF 책임이다.
    ``ErrorCode`` 만 유지(SSE errorCode 정본 값).
--------------------------------------------------
[호환성]
  - Python 3.11.x
--------------------------------------------------
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    """api-spec.md Error Response 표의 표준 코드.

    각 코드는 BFF/프론트가 분기 처리하는 식별자다. ``RETRIEVAL_EMPTY`` /
    ``LOW_CONFIDENCE`` / ``VERIFICATION_BLOCKED`` 도 api-spec.md 표에 포함되어
    있으나, 본 구현에서는 그 세 분기를 200 SSE 성공 응답 내부에서 처리한다
    (`feedback_enabled` / 답변 대체). 본 앱이 실제로 송신하는 값은 SSE ``error``
    이벤트의 ML_* 3종뿐이며, 나머지는 spec 표 호환을 위해 정의만 유지한다.
    """

    UNAUTHORIZED = "UNAUTHORIZED"
    RETRIEVAL_EMPTY = "RETRIEVAL_EMPTY"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    UPSTREAM_LLM_ERROR = "UPSTREAM_LLM_ERROR"
    VERIFICATION_BLOCKED = "VERIFICATION_BLOCKED"
    # api-spec v2.2.0 §1-1 "error 이벤트 / 에러 코드" 정본 3종. SSE ``error`` 이벤트의
    # ``errorCode`` 는 반드시 이 세 값 중 하나여야 한다(BFF 가 그대로 중계 — §2-1).
    #   ML_SERVER_ERROR     — ML 서버 5xx·내부 처리 오류
    #   ML_TIMEOUT          — ML 응답/스트림 타임아웃 (lina.rag.sse-timeout-ms)
    #   ML_CONNECTION_ERROR — ML 연결 실패·스트림 중단
    ML_SERVER_ERROR = "ML_SERVER_ERROR"
    ML_TIMEOUT = "ML_TIMEOUT"
    ML_CONNECTION_ERROR = "ML_CONNECTION_ERROR"


# NOTE(2026-06-10, P4): 종전의 비-SSE 4필드 에러 봉투 머신(ErrorResponse /
# HTTP_STATUS_BY_CODE / error_response)은 호출처가 없어 삭제했다. FE↔BFF 공통 Wrapper
# 직렬화는 BFF ``common`` 모듈 책임이고, 본 앱의 오류 표면은 SSE ``error`` 이벤트
# (`routes._error_event` — errorCode/message)뿐이다. 비-SSE 오류 응답이 필요해지면
# git 이력(2026-05-29 판)에서 복원한다.
