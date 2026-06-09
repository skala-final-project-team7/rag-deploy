"""ACL Pre-filtering — JWT 클레임 추출 + Qdrant 필터 생성 + @enforce_acl.

--------------------------------------------------
작성자 : 최태성
작성목적 : LINA RAG 파이프라인의 사용자 단위 검색 권한 경계를 시스템 단에서 강제한다.
          BFF가 전달한 JWT에서 사용자 식별(user_id/groups)을 추출하고, Qdrant 검색에
          항상 주입되는 ACL 필터를 생성하며, ACL 필터 없는 검색 호출을 데코레이터로
          거부한다 (rag-pipeline-design.md §6 4.2, app/CLAUDE.md §3, db-schema.md §1.4).
작성일 : 2026-05-15
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-15, 최초 작성, feature7 — extract_principal / build_acl_filter / @enforce_acl
  - 2026-05-17, 코드 리뷰 후속(P1-2) — _is_valid_acl_filter가 should 절 내부 구조까지
    검사하도록 강화 (잘못된 호출 조기 감지). enforce_acl docstring을 coroutine 반환 함수
    한정으로 정정
  - 2026-06-04, allow_authenticated 연동 — build_acl_filter가 모든 principal 의
    allowed_groups 매칭에 PUBLIC_ACL_GROUP("*") sentinel 을 항상 주입하도록 변경. Ingestion
    의 allow_authenticated 정책(restriction 없는 페이지에 "*" 부여)과 짝을 이루는 공유
    계약이다 (docs/db-schema.md §1.4, ADR 0003)
--------------------------------------------------
[호환성]
  - Python 3.11.x, Pydantic 2.7+
  - NOTE: JWT 서명 검증·발급은 BFF(Authorization Server) 책임이다 (api-spec.md).
          본 모듈은 BFF가 검증을 마친 JWT에서 클레임을 '추출'만 한다.
--------------------------------------------------
"""

import base64
import binascii
import inspect
import json
from collections.abc import Callable
from functools import wraps
from typing import Any

from pydantic import BaseModel, Field


class PrincipalExtractionError(Exception):
    """JWT에서 사용자 식별(user_id/groups) 추출에 실패했을 때 발생한다.

    API 계층에서 `UNAUTHORIZED`(401) 응답으로 매핑된다 (docs/api-spec.md).
    """


class ACLViolationError(Exception):
    """ACL 필터 없이 검색 함수를 호출하려 했을 때 @enforce_acl이 발생시킨다.

    검색 결과의 권한 경계를 시스템 단에서 보장하기 위한 안전장치이며,
    이 예외가 발생했다면 호출 측 코드가 ACL 주입을 누락한 것이다.
    """


class Principal(BaseModel):
    """JWT에서 추출한 검색 주체. ACL 필터 생성과 RagState 입력에 사용된다."""

    user_id: str
    groups: list[str] = Field(default_factory=list)


def extract_principal(jwt: str) -> Principal:
    """BFF가 전달한 JWT에서 사용자 식별(user_id/groups)을 추출한다.

    JWT payload(두 번째 세그먼트)를 base64url 디코드해 클레임을 읽는다. 서명은
    검증하지 않는다 — 인증·서명 검증·토큰 발급은 BFF(Authorization Server)의 책임이며
    (docs/api-spec.md), 본 함수는 검증이 끝난 토큰에서 클레임만 추출한다.

    Args:
        jwt: BFF가 전달한 JWT 문자열 (`header.payload.signature`).

    Returns:
        `sub`(user_id)와 `groups`를 담은 Principal. `groups` 클레임이 없으면 빈 목록.

    Raises:
        PrincipalExtractionError: JWT 형식 오류, payload 디코드 실패, `sub` 클레임
            누락, `groups` 클레임이 배열이 아닌 경우. API의 `UNAUTHORIZED`에 대응한다.
    """
    segments = jwt.split(".")
    if len(segments) != 3:
        raise PrincipalExtractionError(
            "JWT 형식이 올바르지 않습니다 (header.payload.signature 필요)"
        )

    payload_segment = segments[1]
    # base64url은 4의 배수 길이를 요구하므로 누락된 패딩을 보정한다.
    padding = "=" * (-len(payload_segment) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(payload_segment + padding)
        claims = json.loads(payload_bytes)
    except (binascii.Error, ValueError) as exc:
        raise PrincipalExtractionError("JWT payload를 디코드하지 못했습니다") from exc

    if not isinstance(claims, dict):
        raise PrincipalExtractionError("JWT payload가 JSON 객체가 아닙니다")

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise PrincipalExtractionError("JWT에 sub(user_id) 클레임이 없습니다")

    raw_groups = claims.get("groups", [])
    if not isinstance(raw_groups, list):
        raise PrincipalExtractionError("JWT groups 클레임이 배열이 아닙니다")

    return Principal(user_id=user_id, groups=[str(group) for group in raw_groups])


# "모든 인증 사용자 허용" sentinel group — Ingestion 이 page-level read restriction 이 없는
# (조직 내 누구나 열람 가능한) 페이지의 allowed_groups 에 부여하는 토큰이다. 모든 principal 의
# 검색 ACL 필터에 항상 주입해 인증 사용자라면 누구나 public 청크에 매칭되게 한다.
# [공유 계약] Ingestion 측 ``atlassian_public_acl_group`` (기본 "*") 과 반드시 동일해야 한다
# (ingestion↔rag — docs/db-schema.md §1.4, ADR 0003). 한쪽만 바꾸면 public 페이지가 검색에서
# 사라지거나 과다 노출된다.
PUBLIC_ACL_GROUP = "*"


def build_acl_filter(user_id: str, groups: list[str]) -> dict[str, Any]:
    """사용자 식별로 Qdrant ACL 필터(`should` = OR 결합)를 생성한다.

    청크의 `allowed_groups`가 사용자 그룹 중 하나와 매칭되거나(OR) `allowed_users`가
    user_id를 포함하면 접근 가능하다. 이 필터는 @enforce_acl을 통과한 검색 호출에서
    다른 메타 필터와 `AND`로 결합된다 (db-schema.md §1.4).

    모든 인증 사용자에게 열린 페이지(Ingestion 의 ``allow_authenticated`` 정책)는
    allowed_groups 에 ``PUBLIC_ACL_GROUP`` sentinel 을 갖는다. 따라서 사용자 그룹 목록에
    이 sentinel 을 항상 추가해, 인증된 사용자라면 그룹이 없어도 public 청크를 볼 수 있게 한다.

    ACL 필드 모델은 `allowed_groups`/`allowed_users`로 결정되었다. 모델이 바뀌면
    (예: space_key 기반) 이 함수만 교체하면 되도록 필터 생성 로직을 여기에 격리한다
    (app/CLAUDE.md §3).

    Args:
        user_id: 검색 주체의 사용자 식별자.
        groups: 사용자가 속한 그룹 목록. 비어 있어도 된다.

    Returns:
        Qdrant 필터 dict (`{"should": [...]}`). RagState.acl_filter 계약(dict)과 정합한다.
    """
    group_terms = list(groups)
    if PUBLIC_ACL_GROUP not in group_terms:
        group_terms.append(PUBLIC_ACL_GROUP)
    return {
        "should": [
            {"key": "allowed_groups", "match": {"any": group_terms}},
            {"key": "allowed_users", "match": {"any": [user_id]}},
        ]
    }


def _is_valid_acl_clause(clause: object) -> bool:
    """단일 ACL 절(should 항목)이 build_acl_filter 산출물 형태인지 검사한다.

    절은 ``{"key": <str>, "match": {"any": <list>}}`` 형태여야 한다. 'key'·'match'·
    'match.any' 중 하나라도 빠지거나 타입이 어긋나면 거부한다 (P1-2 강화).
    """
    if not isinstance(clause, dict):
        return False
    key = clause.get("key")
    match = clause.get("match")
    if not isinstance(key, str) or not key:
        return False
    if not isinstance(match, dict):
        return False
    return isinstance(match.get("any"), list)


def _is_valid_acl_filter(acl_filter: object) -> bool:
    """ACL 필터가 build_acl_filter 산출물 형태인지 검사한다.

    필터 누락(None)·빈 dict·비-dict·`should` 절 부재·절 내부 구조 오류를 모두 거부한다.
    구조적 누락을 잡기 위한 검사이며, 위조 방지가 아니라 'ACL 주입을 잊지 않도록'
    강제하는 목적이다. P1-2(코드 리뷰 후속)에서 절 내부 검사를 추가했다.
    """
    if not isinstance(acl_filter, dict):
        return False
    should = acl_filter.get("should")
    if not isinstance(should, list) or not should:
        return False
    return all(_is_valid_acl_clause(clause) for clause in should)


def enforce_acl(func: Callable[..., Any]) -> Callable[..., Any]:
    """검색 함수에 유효한 `acl_filter` 인자가 주입됐는지 강제하는 데코레이터.

    Qdrant 검색 호출은 반드시 이 데코레이터를 통과해야 하며, ACL 필터가 없거나
    무효이면 `ACLViolationError`로 거부한다 (app/CLAUDE.md §3 — ACL Pre-filtering 우회 금지).

    데코레이션 시점에 대상 함수가 `acl_filter` 파라미터를 갖는지 검사하고, 호출 시점에
    그 인자가 유효한 ACL 필터인지 검사한다. ACL 검사는 함수 호출 이전에 끝나기 때문에
    coroutine 반환 함수에도 적용할 수 있다 — wrapper 자체는 동기로 검사하고 원본 함수의
    반환값(coroutine)을 그대로 돌려준다(호출자가 ``await`` 책임).

    Args:
        func: `acl_filter` 파라미터를 갖는 검색 함수.

    Returns:
        호출 전 ACL 필터를 검증하는 래퍼 함수.

    Raises:
        TypeError: 대상 함수에 `acl_filter` 파라미터가 없을 때 (데코레이션 시점).
    """
    signature = inspect.signature(func)
    if "acl_filter" not in signature.parameters:
        raise TypeError(
            f"@enforce_acl 대상 함수 '{func.__name__}'에 acl_filter 파라미터가 필요합니다"
        )

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        if not _is_valid_acl_filter(bound.arguments.get("acl_filter")):
            raise ACLViolationError(
                f"'{func.__name__}' 호출에 유효한 ACL 필터가 없습니다 — ACL Pre-filtering 우회 거부"
            )
        return func(*args, **kwargs)

    return wrapper
