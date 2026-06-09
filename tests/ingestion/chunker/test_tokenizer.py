"""count_tokens — PoC 토큰 카운터 검증 (chunking-strategy.md §7)."""

from app.ingestion.chunker.tokenizer import count_tokens


def test_empty_text_is_zero() -> None:
    assert count_tokens("") == 0
    assert count_tokens("   \n  ") == 0


def test_ascii_words_counted_by_whitespace() -> None:
    assert count_tokens("hello world") == 2
    assert count_tokens("kubectl get pods") == 3


def test_cjk_counted_per_character() -> None:
    # 한글은 글자 단위로 센다
    assert count_tokens("안녕하세요") == 5


def test_mixed_korean_english() -> None:
    # "EKS"(1) + 노/드/조/인/실/패(6) = 7
    assert count_tokens("EKS 노드 조인 실패") == 7


def test_longer_text_has_more_tokens() -> None:
    short = count_tokens("짧은 문장")
    long = count_tokens("이것은 훨씬 더 긴 문장이고 토큰 수가 더 많아야 한다")
    assert long > short
