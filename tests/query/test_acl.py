"""ACL Pre-filtering 검증 (feature7) — rag-pipeline-design.md §6, db-schema.md §1.4.

extract_principal(JWT 클레임 추출), build_acl_filter(Qdrant should 필터 생성),
@enforce_acl(ACL 없는 검색 호출 거부)을 검증한다.
"""

import base64
import json

import pytest

from app.query.acl import (
    PUBLIC_ACL_GROUP,
    ACLViolationError,
    Principal,
    PrincipalExtractionError,
    build_acl_filter,
    enforce_acl,
    extract_principal,
)


def _make_jwt(claims: dict, *, signature: str = "sig") -> str:
    """테스트용 JWT 문자열 — 서명은 검증되지 않으므로 임의 값을 쓴다."""

    def _segment(payload: dict) -> str:
        raw = json.dumps(payload).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{_segment({'alg': 'none'})}.{_segment(claims)}.{signature}"


def _valid_filter() -> dict:
    return build_acl_filter("taesung", ["sre"])


# --- extract_principal ---


def test_extract_principal_from_valid_jwt() -> None:
    jwt = _make_jwt({"sub": "taesung", "groups": ["cloud-platform", "sre"]})
    principal = extract_principal(jwt)
    assert isinstance(principal, Principal)
    assert principal.user_id == "taesung"
    assert principal.groups == ["cloud-platform", "sre"]


def test_extract_principal_groups_default_to_empty() -> None:
    # groups 클레임이 없으면 빈 목록 (allowed_users 매칭만으로 접근)
    principal = extract_principal(_make_jwt({"sub": "taesung"}))
    assert principal.groups == []


def test_extract_principal_rejects_malformed_jwt() -> None:
    # 세그먼트가 3개가 아니면 형식 오류
    with pytest.raises(PrincipalExtractionError):
        extract_principal("not-a-jwt")
    with pytest.raises(PrincipalExtractionError):
        extract_principal("only.two")


def test_extract_principal_rejects_undecodable_payload() -> None:
    # payload가 JSON이 아니면 추출 실패
    bad_payload = base64.urlsafe_b64encode(b"not json").decode("ascii").rstrip("=")
    with pytest.raises(PrincipalExtractionError):
        extract_principal(f"header.{bad_payload}.sig")


def test_extract_principal_rejects_missing_sub() -> None:
    # sub(user_id) 클레임이 없으면 추출 실패 (API의 UNAUTHORIZED 대응)
    with pytest.raises(PrincipalExtractionError):
        extract_principal(_make_jwt({"groups": ["sre"]}))


def test_extract_principal_rejects_non_list_groups() -> None:
    # groups 클레임이 배열이 아니면 추출 실패 (P2 회귀 보호)
    with pytest.raises(PrincipalExtractionError):
        extract_principal(_make_jwt({"sub": "taesung", "groups": "sre"}))
    with pytest.raises(PrincipalExtractionError):
        extract_principal(_make_jwt({"sub": "taesung", "groups": {"role": "sre"}}))


# --- build_acl_filter ---


def test_build_acl_filter_has_should_or_structure() -> None:
    acl_filter = build_acl_filter("taesung", ["cloud-platform", "sre"])
    should = acl_filter["should"]
    assert isinstance(should, list) and len(should) == 2
    by_key = {clause["key"]: clause["match"]["any"] for clause in should}
    # allowed_groups 가 사용자 그룹 중 하나와 매칭 OR allowed_users 가 user_id 포함.
    # 모든 인증 사용자 public sentinel("*")이 그룹 목록 끝에 항상 주입된다.
    assert by_key["allowed_groups"] == ["cloud-platform", "sre", PUBLIC_ACL_GROUP]
    assert by_key["allowed_users"] == ["taesung"]


def test_build_acl_filter_handles_empty_groups() -> None:
    acl_filter = build_acl_filter("taesung", [])
    by_key = {clause["key"]: clause["match"]["any"] for clause in acl_filter["should"]}
    # 그룹이 없어도 public sentinel 로 public 청크 매칭 + allowed_users 절로 본인 청크 접근
    assert by_key["allowed_groups"] == [PUBLIC_ACL_GROUP]
    assert by_key["allowed_users"] == ["taesung"]


def test_build_acl_filter_always_injects_public_sentinel() -> None:
    # 인증 사용자라면 그룹 유무와 무관하게 public("*") 청크에 매칭돼야 한다.
    assert PUBLIC_ACL_GROUP in build_acl_filter("u", [])["should"][0]["match"]["any"]
    assert PUBLIC_ACL_GROUP in build_acl_filter("u", ["sre"])["should"][0]["match"]["any"]


def test_build_acl_filter_does_not_duplicate_sentinel() -> None:
    # 사용자가 이미 "*" 그룹을 가진 경우 sentinel 을 중복 추가하지 않는다.
    groups_field = build_acl_filter("u", [PUBLIC_ACL_GROUP])["should"][0]["match"]["any"]
    assert groups_field.count(PUBLIC_ACL_GROUP) == 1


def test_build_acl_filter_does_not_alias_groups() -> None:
    groups = ["sre"]
    acl_filter = build_acl_filter("taesung", groups)
    groups.append("admin")  # 원본 리스트 변경이 필터에 영향을 주면 안 된다
    assert acl_filter["should"][0]["match"]["any"] == ["sre", PUBLIC_ACL_GROUP]


# --- @enforce_acl ---


def test_enforce_acl_allows_call_with_valid_filter() -> None:
    @enforce_acl
    def search(query: str, *, acl_filter: dict | None = None) -> str:
        return f"results for {query}"

    assert search("eks 장애", acl_filter=_valid_filter()) == "results for eks 장애"


def test_enforce_acl_rejects_missing_filter() -> None:
    @enforce_acl
    def search(query: str, *, acl_filter: dict | None = None) -> str:
        return "results"

    # acl_filter 미전달(기본값 None) → 거부
    with pytest.raises(ACLViolationError):
        search("eks 장애")
    # 명시적 None → 거부
    with pytest.raises(ACLViolationError):
        search("eks 장애", acl_filter=None)


def test_enforce_acl_rejects_invalid_filter() -> None:
    @enforce_acl
    def search(query: str, *, acl_filter: dict | None = None) -> str:
        return "results"

    # 빈 dict·should 없는 dict·dict 아닌 값 모두 거부
    with pytest.raises(ACLViolationError):
        search("q", acl_filter={})
    with pytest.raises(ACLViolationError):
        search("q", acl_filter={"must": []})
    with pytest.raises(ACLViolationError):
        search("q", acl_filter="not-a-filter")


def test_enforce_acl_rejects_malformed_should_clauses() -> None:
    # P1-2: should 절 내부 구조까지 검사 — key/match.any 누락·타입 오류 모두 거부
    @enforce_acl
    def search(query: str, *, acl_filter: dict | None = None) -> str:
        return "results"

    with pytest.raises(ACLViolationError):
        search("q", acl_filter={"should": []})
    with pytest.raises(ACLViolationError):
        search("q", acl_filter={"should": ["not-a-clause"]})
    with pytest.raises(ACLViolationError):
        search("q", acl_filter={"should": [{"match": {"any": ["sre"]}}]})
    with pytest.raises(ACLViolationError):
        search("q", acl_filter={"should": [{"key": "allowed_groups"}]})
    with pytest.raises(ACLViolationError):
        search(
            "q",
            acl_filter={"should": [{"key": "allowed_groups", "match": {"any": "sre"}}]},
        )


def test_enforce_acl_accepts_positional_filter() -> None:
    @enforce_acl
    def search(acl_filter: dict, query: str) -> str:
        return "results"

    # 위치 인자로 전달해도 동작한다
    assert search(_valid_filter(), "q") == "results"
    with pytest.raises(ACLViolationError):
        search({}, "q")


def test_enforce_acl_requires_acl_filter_parameter() -> None:
    # acl_filter 파라미터가 없는 함수는 데코레이션 시점에 거부 (구조적 강제)
    with pytest.raises(TypeError):

        @enforce_acl
        def search_without_acl(query: str) -> str:
            return "results"


# --- 통합: JWT → Principal → 필터 → @enforce_acl ---


def test_principal_filter_passes_enforce_acl() -> None:
    principal = extract_principal(_make_jwt({"sub": "taesung", "groups": ["sre"]}))
    acl_filter = build_acl_filter(principal.user_id, principal.groups)

    @enforce_acl
    def search(query: str, *, acl_filter: dict | None = None) -> str:
        return "ok"

    assert search("q", acl_filter=acl_filter) == "ok"


def test_enforce_acl_returns_coroutine_for_async_target() -> None:
    # 비동기 검색 함수에 데코레이션해도 ACL 검사가 호출 전에 끝난 뒤 coroutine을 그대로
    # 돌려준다. ACL 누락 시는 ACLViolationError로 즉시 거부.
    import asyncio

    @enforce_acl
    async def async_search(query: str, *, acl_filter: dict | None = None) -> str:
        return f"async results for {query}"

    coro = async_search("q", acl_filter=_valid_filter())
    assert asyncio.iscoroutine(coro)
    assert asyncio.run(coro) == "async results for q"
    with pytest.raises(ACLViolationError):
        async_search("q", acl_filter=None)
