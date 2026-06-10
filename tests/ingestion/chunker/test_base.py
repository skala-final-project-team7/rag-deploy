"""청킹 2단계 하이브리드 규칙 — split_oversized / merge_undersized / apply_size_rules.

chunking-strategy.md §3·§5: 1차 분할(body.py 담당) 결과에 2차 재분할(800토큰 초과 →
100토큰 오버랩)과 하한선 병합(200토큰 미만)을 적용. 원자성 유지 유형은 제외.

OVERSIZE_ATOMIC(§8, 2026-06-10 코드 리뷰 P2-9): 원자성 청크도 ATOMIC_MAX_TOKENS(1500)
초과 시 강제 분할 + ``section_header`` 에 ``(Part N/M)`` 표기(is_atomic 유지). 1500
이하 원자성 청크는 800토큰 임계를 넘어도 종전대로 비분할.
"""

from app.ingestion.chunker.base import (
    ATOMIC_MAX_TOKENS,
    MAX_TOKENS,
    ChunkDraft,
    apply_size_rules,
    merge_undersized,
    split_oversized,
)
from app.ingestion.chunker.tokenizer import count_tokens


def test_split_oversized_keeps_short_text() -> None:
    text = "짧은 문장 하나"
    assert split_oversized(text, max_tokens=100, overlap_tokens=10) == [text]


def test_split_oversized_splits_long_text() -> None:
    # 10줄, 각 줄 약 4토큰 → 약 40토큰. max=12면 여러 윈도우로 분할
    text = "\n".join(f"라인 번호 {i}" for i in range(10))
    windows = split_oversized(text, max_tokens=12, overlap_tokens=4)
    assert len(windows) > 1
    # 각 윈도우는 max_tokens 이하 (단일 단위 예외 제외)
    for window in windows:
        assert count_tokens(window) <= 12
    # 전체 라인이 누락 없이 포함된다
    joined = "\n".join(windows)
    for i in range(10):
        assert f"라인 번호 {i}" in joined


def test_split_oversized_has_overlap() -> None:
    text = "\n".join(f"문장{i}" for i in range(12))
    windows = split_oversized(text, max_tokens=6, overlap_tokens=2)
    # 인접 윈도우는 겹치는 라인을 공유한다
    assert len(windows) >= 2
    first_lines = set(windows[0].split("\n"))
    second_lines = set(windows[1].split("\n"))
    assert first_lines & second_lines


def test_merge_undersized_merges_small_adjacent() -> None:
    drafts = [
        ChunkDraft(text="작은 비원자 청크", section_header="A"),  # < 200 토큰 (prev, 미봉인)
        ChunkDraft(text="짧음", section_header="B"),  # < 200 토큰 → 직전과 병합
    ]
    merged = merge_undersized(drafts, min_tokens=200)
    assert len(merged) == 1
    assert "짧음" in merged[0].text


def test_merge_undersized_seals_chunk_at_min_tokens() -> None:
    # 회귀(LINA 버그): 작은 청크가 직전 청크에 무한 누적되어 한 청크로 붕괴하면 안 된다.
    # 직전 청크가 하한선(min_tokens)을 채우면 '봉인'되어 이후 작은 청크는 새 청크가 된다
    # (chunking-strategy.md §3 — 하한선 처리는 '직전/직후' 1회 병합 의도).
    drafts = [ChunkDraft(text="가 나 다 라 마", section_header=f"S{i}") for i in range(20)]
    merged = merge_undersized(drafts, min_tokens=12)
    # 20개 작은 청크가 한 청크로 붕괴하지 않는다
    assert len(merged) > 1
    # 봉인된 청크(마지막 꼬리 제외)는 모두 하한선 이상
    for chunk in merged[:-1]:
        assert count_tokens(chunk.text) >= 12


def test_merge_undersized_keeps_atomic() -> None:
    drafts = [
        ChunkDraft(text="가 " * 250, section_header="A"),
        ChunkDraft(text="짧은 원자 청크", section_header="B", is_atomic=True),
    ]
    merged = merge_undersized(drafts, min_tokens=200)
    # 원자성 청크는 작아도 병합되지 않는다
    assert len(merged) == 2


def test_apply_size_rules_combines_split_and_merge() -> None:
    # 약 8토큰 × 250줄 = 2000토큰 → 기본 임계(800) 초과로 2차 재분할 발생
    big_text = "\n".join(f"항목 번호 {i} 입니다" for i in range(250))
    drafts = [
        ChunkDraft(text=big_text, section_header="big"),
        ChunkDraft(text="adr 원자 청크", section_header="adr", is_atomic=True),
    ]
    result = apply_size_rules(drafts)
    # 큰 비원자 청크는 여러 조각으로 분할, 원자 청크는 그대로 유지
    assert len([d for d in result if d.section_header == "big"]) >= 2
    assert any(d.is_atomic and d.section_header == "adr" for d in result)


def test_chunk_draft_defaults() -> None:
    draft = ChunkDraft(text="본문", section_header="섹션")
    assert draft.is_atomic is False


# --- OVERSIZE_ATOMIC 강제 분할 (chunking-strategy.md §8, 코드 리뷰 P2-9) ---


def _atomic_draft(lines: int, *, header: str = "ADR-001") -> ChunkDraft:
    """비-CJK 단어 3개짜리 줄 N개 — count_tokens 가 줄당 정확히 3을 세는 원자 청크."""
    text = "\n".join(f"line token{i} payload" for i in range(lines))
    return ChunkDraft(text=text, section_header=header, is_atomic=True)


def test_atomic_at_or_below_1500_tokens_is_never_split() -> None:
    """1500토큰 이하 원자성 청크는 800토큰 임계를 넘어도 비분할(기존 동작 보존)."""
    draft = _atomic_draft(500)  # 500줄 × 3토큰 = 정확히 ATOMIC_MAX_TOKENS.
    assert count_tokens(draft.text) == ATOMIC_MAX_TOKENS
    assert count_tokens(draft.text) > MAX_TOKENS  # 2차 재분할 임계는 이미 초과 상태.

    result = apply_size_rules([draft])

    assert len(result) == 1
    assert result[0].text == draft.text
    assert result[0].section_header == "ADR-001"  # Part 표기 없음.
    assert result[0].is_atomic is True


def test_atomic_above_1500_tokens_is_force_split_with_part_headers() -> None:
    """1500토큰 초과 원자성 청크 → 강제 분할 + ``(Part N/M)`` 표기 + is_atomic 유지."""
    draft = _atomic_draft(600)  # 600줄 × 3토큰 = 1800 > ATOMIC_MAX_TOKENS.
    assert count_tokens(draft.text) > ATOMIC_MAX_TOKENS

    result = apply_size_rules([draft])

    assert len(result) >= 2
    total = len(result)
    for index, part in enumerate(result, start=1):
        # 원자성 유지 — 분할 파트는 하한선 병합 대상이 되지 않는다.
        assert part.is_atomic is True
        assert part.section_header == f"ADR-001 (Part {index}/{total})"
        # 각 파트는 임베딩 입력 윈도 보호 임계(MAX_TOKENS=800) 이하.
        assert count_tokens(part.text) <= MAX_TOKENS
    # 본문 누락 없음 — 모든 줄이 어느 파트에든 포함된다(오버랩 중복은 허용).
    joined = "\n".join(part.text for part in result)
    for i in range(600):
        assert f"token{i} payload" in joined


def test_atomic_oversize_parts_not_merged_with_neighbors() -> None:
    """분할된 Part 들은 원자성이라 인접 비원자 청크와 병합되지 않는다."""
    drafts = [
        _atomic_draft(600),
        ChunkDraft(text="후속 비원자 청크", section_header="다음 섹션"),
    ]
    result = apply_size_rules(drafts)
    # Part 청크들 + 마지막 비원자 청크가 각각 보존된다.
    assert result[-1].section_header == "다음 섹션"
    assert result[-1].is_atomic is False
    assert all(part.is_atomic for part in result[:-1])
    assert all("(Part " in part.section_header for part in result[:-1])
