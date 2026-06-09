"""본문 6유형 1차 분할기 검증 (chunking-strategy.md §3·§4).

split_body는 body_html을 받아 clean_storage_format으로 정제 후 doc_type별로 분할한다.
"""

from app.ingestion.chunker.base import ChunkDraft
from app.ingestion.chunker.body import split_body
from app.schemas.enums import DocType

_OPERATION_HTML = "<h2>설치</h2><p>설치 절차</p><h2>설정</h2><p>설정 절차</p>"
_INCIDENT_HTML = "<h2>타임라인</h2><p>14:02 장애</p><h2>원인</h2><p>IAM 정책</p>"
_FAQ_HTML = "<h3>설치는 어떻게 하나요?</h3><p>1줄 설치</p><h3>삭제는?</h3><p>helm uninstall</p>"
_MEETING_HTML = (
    "<p>[일자] 2026-01-01 [참석] 최태성</p>"
    "<h2>안건 1: R&R</h2><p>분담 결정</p><h2>안건 2: 일정</h2><p>8주 PoC</p>"
)
_TROUBLESHOOT_HTML = (
    "<h3>노드 조인 실패</h3><p>증상: 조인 안 됨</p>"
    "<h3>CrashLoopBackOff</h3><p>증상: 재시작 반복</p>"
)
_ADR_HTML = "<h2>ADR-007 Qdrant 채택</h2><p>맥락</p><p>결정</p><p>근거</p>"


def test_empty_body_returns_empty() -> None:
    assert split_body("", DocType.OPERATION) == []
    assert split_body("   ", DocType.OPERATION) == []


def test_operation_splits_by_h2_not_atomic() -> None:
    drafts = split_body(_OPERATION_HTML, DocType.OPERATION)
    assert len(drafts) == 2
    assert all(isinstance(d, ChunkDraft) for d in drafts)
    assert {d.section_header for d in drafts} == {"설치", "설정"}
    assert all(d.is_atomic is False for d in drafts)


def test_incident_splits_by_h2_atomic() -> None:
    drafts = split_body(_INCIDENT_HTML, DocType.INCIDENT)
    assert len(drafts) == 2
    assert {d.section_header for d in drafts} == {"타임라인", "원인"}
    # 장애대응 블록은 원자성 유지
    assert all(d.is_atomic is True for d in drafts)


def test_faq_splits_into_atomic_qa_pairs() -> None:
    drafts = split_body(_FAQ_HTML, DocType.FAQ)
    assert len(drafts) == 2
    assert all(d.is_atomic is True for d in drafts)
    # 질문과 답이 한 청크에 함께 있어야 한다
    assert any("1줄 설치" in d.text and "설치는 어떻게" in d.text for d in drafts)


def test_meeting_prepends_metadata_to_each_agenda() -> None:
    drafts = split_body(_MEETING_HTML, DocType.MEETING)
    assert len(drafts) == 2
    assert all(d.is_atomic is True for d in drafts)
    # 상단 메타(일자/참석)가 각 안건 청크 도입부에 부착된다
    assert all("[일자] 2026-01-01" in d.text for d in drafts)


def test_troubleshoot_splits_cases_atomic() -> None:
    drafts = split_body(_TROUBLESHOOT_HTML, DocType.TROUBLESHOOT)
    assert len(drafts) == 2
    assert all(d.is_atomic is True for d in drafts)


def test_adr_is_single_atomic_chunk() -> None:
    drafts = split_body(_ADR_HTML, DocType.ADR)
    assert len(drafts) == 1
    assert drafts[0].is_atomic is True
    assert "맥락" in drafts[0].text and "결정" in drafts[0].text


def test_headingless_body_falls_back_to_single_draft() -> None:
    drafts = split_body("<p>헤딩 없는 본문 문장</p>", DocType.OPERATION)
    assert len(drafts) == 1
    assert "헤딩 없는 본문 문장" in drafts[0].text


def test_unknown_doc_type_defaults_to_operation() -> None:
    # 미인식 doc_type은 operation으로 폴백 (chunking-strategy.md §6.3)
    drafts = split_body(_OPERATION_HTML, "unknown-type")
    assert len(drafts) == 2
    assert all(d.is_atomic is False for d in drafts)
