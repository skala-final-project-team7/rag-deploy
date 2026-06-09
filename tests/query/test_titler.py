"""대화 제목 생성기(app/query/titler.py) 단위 테스트.

LLM 호출(`generate_conversation_title`)은 외부 의존성이므로 본 단위 테스트는 네트워크
없이 동작하는 순수 로직(`fallback_title` 및 내부 `_clean_title` 정리 규칙)만 검증한다.
운영 경로의 LLM 호출/실패 fallback 통합은 tests/api/test_query_route.py 에서 라우트
레벨로 회귀한다(titler monkeypatch).
"""

from app.query.titler import _clean_title, fallback_title


def test_fallback_title_truncates_to_max_len() -> None:
    """30자를 초과하는 질문은 30자로 절단한다."""
    question = "가" * 50
    title = fallback_title(question)
    assert len(title) == 30
    assert title == "가" * 30


def test_fallback_title_empty_question_returns_default() -> None:
    """빈/공백 질문은 기본 제목('새 대화')을 반환해 meta.title 이 비지 않게 한다."""
    assert fallback_title("") == "새 대화"
    assert fallback_title("   \n\t ") == "새 대화"


def test_fallback_title_collapses_whitespace() -> None:
    """여러 줄·연속 공백은 단일 공백으로 정규화한다."""
    assert fallback_title("S3   권한\n오류") == "S3 권한 오류"


def test_clean_title_strips_quotes_and_prefix() -> None:
    """LLM 이 따옴표로 감싸거나 '제목:' 접두어를 붙여도 제거한다."""
    assert _clean_title('"S3 권한 오류 해결"') == "S3 권한 오류 해결"
    assert _clean_title("제목: EKS 운영 가이드") == "EKS 운영 가이드"
    assert _clean_title("“ 따옴표 제거 ”") == "따옴표 제거"


def test_clean_title_truncates() -> None:
    """정리 후에도 30자를 초과하면 절단한다."""
    assert len(_clean_title("나" * 40)) == 30
