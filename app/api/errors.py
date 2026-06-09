"""Error Response 정의 — api-spec.md Error Response 표 정합 [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : RAG 파이프라인 HTTP 계층의 Error Response 스키마와 표준 코드를 정의한다.
          `RETRIEVAL_EMPTY` / `LOW_CONFIDENCE` / `VERIFICATION_BLOCKED` 같은 "표준
          분기 응답"은 200 SSE 성공 응답 내부에서 처리되므로(`feedback_enabled=False`
          또는 답변 대체) 본 모듈의 Error Response는 `UNAUTHORIZED`(JWT 추출 실패) 와
          `UPSTREAM_LLM_ERROR`(LLM 호출 실패 / 타임아웃) 같은 본격 오류에만 사용된다.
작성일 : 2026-05-18
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-18, 최초 작성, feature11 통합 Phase 2 — ErrorCode StrEnum +
    ErrorDetail / ErrorResponse Pydantic 모델 + HTTP status 매핑
  - 2026-05-29, api-spec v2.2.0 정합 — ``ErrorResponse`` 를 공통 Wrapper 의 4필드 봉투
    (``isSuccess`` / ``code`` / ``errorCode`` / ``message``)로 재정의(구 ``success`` +
    중첩 ``error`` 형태 제거). 중첩 ``ErrorDetail`` 모델 삭제, ``error_response`` 헬퍼가
    HTTP ``code`` 를 ``HTTP_STATUS_BY_CODE`` 에서 도출하도록 변경.
--------------------------------------------------
[호환성]
  - Python 3.11.x, Pydantic 2.7+, FastAPI 0.111+
--------------------------------------------------
"""

from enum import StrEnum

from fastapi import status
from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
    """api-spec.md Error Response 표의 표준 코드.

    각 코드는 BFF/프론트가 분기 처리하는 식별자다. ``RETRIEVAL_EMPTY`` /
    ``LOW_CONFIDENCE`` / ``VERIFICATION_BLOCKED`` 도 api-spec.md 표에 포함되어
    있으나, 본 구현에서는 그 세 분기를 200 SSE 성공 응답 내부에서 처리한다
    (`feedback_enabled` / 답변 대체). 본 Enum은 4xx/5xx 응답에만 쓰이는 코드를
    명시하지만 호환성을 위해 모두 정의해 둔다.
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


class ErrorResponse(BaseModel):
    """api-spec v2.2.0 "Common Response Wrapper" 의 에러 봉투 — **4필드 고정**.

    성공 응답과 달리 ``data`` 를 포함하지 않으며 다음 4필드로 고정된다:
    ``isSuccess``(false) / ``code``(HTTP 정수) / ``errorCode``(``ErrorCode`` enum 문자열) /
    ``message``.

    .. code-block:: json

        { "isSuccess": false, "code": 404, "errorCode": "RESOURCE_NOT_FOUND", "message": "..." }

    참고: FE↔BFF 외부 API 의 공통 Wrapper 직렬화는 BFF ``common`` 모듈(``ApiResponse`` /
    ``ErrorResponse``) 책임이며, RAG 파이프라인의 SSE 경로(``/ml/query``)는 Wrapper 미적용
    (이벤트 스트림, ``error`` 이벤트의 ``errorCode``/``message`` 사용)이다. 본 모델은 비-SSE
    오류 응답을 동일 봉투 형식으로 맞추기 위한 정의로, 봉투 필드명을 스펙과 일치시킨다.
    """

    isSuccess: bool = Field(default=False)
    code: int
    errorCode: ErrorCode
    message: str


# 4xx / 5xx 응답에 매핑되는 HTTP status. RETRIEVAL_EMPTY 등의 정상 분기는 200으로
# 처리되므로 본 매핑에 포함하지 않는다 — Error Response로 변환되는 코드만 등록한다.
HTTP_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.UNAUTHORIZED: status.HTTP_401_UNAUTHORIZED,
    ErrorCode.UPSTREAM_LLM_ERROR: status.HTTP_502_BAD_GATEWAY,
}


def error_response(code: ErrorCode, message: str) -> ErrorResponse:
    """Error Response(4필드 봉투) Pydantic 모델을 생성한다 — 라우트 핸들러용 헬퍼.

    HTTP 정수 ``code`` 는 ``HTTP_STATUS_BY_CODE`` 매핑에서 도출하고, 매핑에 없는 코드는
    500(INTERNAL)으로 둔다. ``errorCode`` 에는 인자로 받은 ``ErrorCode`` 를 그대로 싣는다.
    """
    http_code = HTTP_STATUS_BY_CODE.get(code, status.HTTP_500_INTERNAL_SERVER_ERROR)
    return ErrorResponse(code=http_code, errorCode=code, message=message)
