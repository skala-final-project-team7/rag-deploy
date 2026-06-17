"""User-facing citation marker display helpers."""

from app.query.citation_display import CitationMarkerStreamFilter, strip_citation_markers


def test_strip_citation_markers_from_complete_answer() -> None:
    answer = "EKS 노드는 32대입니다 [#1]. 다음 절차를 따릅니다. [#2][#3]"

    assert strip_citation_markers(answer) == "EKS 노드는 32대입니다. 다음 절차를 따릅니다."


def test_stream_filter_removes_markers_split_across_chunks() -> None:
    display_filter = CitationMarkerStreamFilter()

    chunks = ["EKS 노드는 32대입니다 ", "[", "#1", "]. 다음", " 절차", " [#2]", " 완료"]
    rendered = "".join(display_filter.feed(chunk) for chunk in chunks) + display_filter.flush()

    assert rendered == "EKS 노드는 32대입니다. 다음 절차 완료"
    assert "[#1]" not in rendered
    assert "[#2]" not in rendered


def test_stream_filter_preserves_non_marker_brackets() -> None:
    display_filter = CitationMarkerStreamFilter()

    rendered = display_filter.feed("[참고] 값을 확인하세요") + display_filter.flush()

    assert rendered == "[참고] 값을 확인하세요"
