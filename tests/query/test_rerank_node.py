"""Cross-Encoder 재순위화 노드 검증 (feature9-B-3).

FakeCrossEncoderReranker + 임의의 RerankerStub로 candidates → top_chunks + sources의
끝-끝 흐름을 통합 검증한다. 외부 의존성 0.

검증 범위: 단건 ~ 5개 ~ 다수 후보, Top-5 선정, 5위 임계 미만 Top-3 축소, 모든 점수
임계 미만(저신뢰), 빈 candidates short-circuit, contextualized_question 우선, Source
필드 매핑(본문/첨부), score 0~100 변환.
"""

from datetime import datetime

from app.query.rerank import LOW_CONFIDENCE_THRESHOLD, NARROW_SCORE_THRESHOLD
from app.query.rerank_node import cross_encoder_rerank
from app.query.reranker.base import CrossEncoderReranker, FakeCrossEncoderReranker
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import ExtractedFormat, SourceType
from app.schemas.rag_state import HistoryDecision, RagState
from app.storage.chunk_lookup import ChunkLookupRecord, FakeChunkTextLookup

# --- 픽스처·헬퍼 ---


_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _chunk(
    *,
    chunk_id: str,
    text: str = "alpha",
    chunk_index: int = 0,
    is_attachment: bool = False,
    attachment_filename: str | None = None,
) -> Chunk:
    metadata = ChunkMetadata(
        chunk_id=chunk_id,
        page_id="P1",
        page_title="EKS 운영 가이드",
        section_header="개요",
        section_path="Cloud 운영 문서 > 개요",
        chunk_index=chunk_index,
        labels=["eks", "운영"],
        doc_type="operation",
        space_key="CLOUD",
        allowed_groups=["space:CLOUD"],
        allowed_users=[],
        webui_link="/display/CLOUD/eks",
        last_modified=datetime.fromisoformat("2026-04-22T08:15:00+09:00"),
        source_type=SourceType.ATTACHMENT if is_attachment else SourceType.PAGE,
        attachment_id="ATT-1" if is_attachment else None,
        attachment_filename=attachment_filename if is_attachment else None,
        attachment_mime=_DOCX_MIME if is_attachment else None,
        extracted_format=ExtractedFormat.RAW_TEXT if is_attachment else None,
        token_count=120,
    )
    return Chunk(text=text, metadata=metadata)


def _state(
    *,
    query: str = "EKS 운영",
    candidates: list[Chunk] | None = None,
    history_decision: HistoryDecision | None = None,
) -> RagState:
    return RagState(
        query=query,
        user_id="user-test",
        candidates=candidates or [],
        history_decision=history_decision,
    )


class _ConstantScoreReranker(CrossEncoderReranker):
    """모든 passage에 같은 점수를 부여하는 reranker — 임계값 분기 검증용."""

    def __init__(self, score: float) -> None:
        self._score = score

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [self._score] * len(passages)


class _OrderedScoreReranker(CrossEncoderReranker):
    """passage 순서대로 미리 정의된 점수를 부여 — 정렬·축소 검증용."""

    def __init__(self, scores: list[float]) -> None:
        self._scores = scores

    def score(self, query: str, passages: list[str]) -> list[float]:
        if len(passages) != len(self._scores):
            raise ValueError(f"expected {len(self._scores)} passages, got {len(passages)}")
        return list(self._scores)


# --- 빈 candidates short-circuit ---


def test_empty_candidates_clears_top_chunks_and_sources_without_calling_reranker() -> None:
    class _SpyReranker(CrossEncoderReranker):
        def __init__(self) -> None:
            self.call_count = 0

        def score(self, query: str, passages: list[str]) -> list[float]:
            self.call_count += 1
            return [0.0] * len(passages)

    spy = _SpyReranker()
    state = _state(candidates=[])
    result = cross_encoder_rerank(state, reranker=spy)
    assert result.top_chunks == []
    assert result.sources == []
    assert spy.call_count == 0  # 빈 입력에서는 reranker 호출 회피


def test_empty_candidates_resets_previously_set_top_chunks() -> None:
    """이전 노드가 채웠던 top_chunks도 빈 결과로 초기화되어야 한다."""
    state = _state(candidates=[])
    state.top_chunks = [_chunk(chunk_id="stale" * 8)]
    result = cross_encoder_rerank(state, reranker=FakeCrossEncoderReranker())
    assert result.top_chunks == []


# --- 단건 / Top-5 / 다수 ---


def test_single_candidate_returns_single_top_chunk() -> None:
    chunk = _chunk(chunk_id="a" * 40)
    state = _state(candidates=[chunk])
    result = cross_encoder_rerank(state, reranker=_ConstantScoreReranker(score=0.8))
    assert len(result.top_chunks) == 1
    assert result.top_chunks[0].metadata.chunk_id == "a" * 40
    assert len(result.sources) == 1


def test_seven_candidates_select_top_five_by_score() -> None:
    # 7개 후보, reranker가 알파벳 역순으로 점수 부여 (g가 최고, a가 최저)
    chunks = [
        _chunk(chunk_id=letter * 40, chunk_index=idx, text=letter)
        for idx, letter in enumerate("abcdefg")
    ]
    state = _state(candidates=chunks)

    class _LetterScoreReranker(CrossEncoderReranker):
        def score(self, query: str, passages: list[str]) -> list[float]:
            # passages 순서대로 a→0.65, b→0.70, ..., g→0.95 (모두 NARROW 이상)
            return [0.65 + idx * 0.05 for idx, _ in enumerate(passages)]

    result = cross_encoder_rerank(state, reranker=_LetterScoreReranker())
    # 모든 점수가 NARROW_SCORE_THRESHOLD(0.65) 이상이므로 Top-5 유지
    # (feature17c-2: temperature scaling 으로 임계 0.30→0.65 재조정)
    assert len(result.top_chunks) == 5
    # 점수 내림차순 g, f, e, d, c
    ordered_ids = [c.metadata.chunk_id for c in result.top_chunks]
    assert ordered_ids == [letter * 40 for letter in "gfedc"]


# --- Top-3 축소 (5위 점수가 NARROW 임계 미만) ---


def test_narrow_to_top_three_when_fifth_score_below_threshold() -> None:
    # 7개 후보, 처음 3개는 높은 점수, 나머지는 낮은 점수
    chunks = [
        _chunk(chunk_id=letter * 40, chunk_index=idx, text=letter)
        for idx, letter in enumerate("abcdefg")
    ]
    state = _state(candidates=chunks)
    # a/b/c=0.9, d=0.5, e=0.1, f=0.1, g=0.1 — 5위(e)는 NARROW(0.30) 미만
    reranker = _OrderedScoreReranker(scores=[0.9, 0.9, 0.9, 0.5, 0.1, 0.1, 0.1])
    result = cross_encoder_rerank(state, reranker=reranker)
    # 9-A select_reranked 가 Top-3 축소를 적용 — chunk_id 셋만 남아야 함
    assert len(result.top_chunks) == 3
    # 동점은 chunk_id 오름차순(a < b < c) — 9-A 결정론 정합
    ordered_ids = [c.metadata.chunk_id for c in result.top_chunks]
    assert ordered_ids == ["a" * 40, "b" * 40, "c" * 40]


def test_no_narrow_when_fifth_score_at_or_above_threshold() -> None:
    chunks = [
        _chunk(chunk_id=letter * 40, chunk_index=idx, text=letter)
        for idx, letter in enumerate("abcdef")
    ]
    state = _state(candidates=chunks)
    # 5위 == NARROW_SCORE_THRESHOLD 정확히 — 축소 안 됨 (strict less than).
    # 정렬 후 5위가 NARROW 가 되도록 1~4위는 NARROW 초과로 둔다.
    reranker = _OrderedScoreReranker(scores=[0.9, 0.8, 0.75, 0.7, NARROW_SCORE_THRESHOLD, 0.0])
    result = cross_encoder_rerank(state, reranker=reranker)
    assert len(result.top_chunks) == 5


# --- 저신뢰 분기 (모든 점수가 LOW_CONFIDENCE_THRESHOLD 미만) ---


def test_all_low_scores_yield_low_confidence_source_scores() -> None:
    chunks = [_chunk(chunk_id="a" * 40), _chunk(chunk_id="b" * 40, chunk_index=1)]
    state = _state(candidates=chunks)
    # 모든 점수 LOW(0.55) 미만 (feature17c-2: 임계 0.20→0.55 재조정)
    reranker = _ConstantScoreReranker(score=0.10)
    result = cross_encoder_rerank(state, reranker=reranker)
    # Source.score는 정수 0~100 — 모든 score가 임계(55=LOW*100) 미만
    assert all(src.score < 55 for src in result.sources)
    # 본 노드는 is_low_confidence를 별도로 두지 않는다 — 포맷터(feature11)가 score로 판정


# --- contextualized_question 우선 사용 ---


def test_contextualized_question_is_used_when_present() -> None:
    received_queries: list[str] = []

    class _QueryCaptureReranker(CrossEncoderReranker):
        def score(self, query: str, passages: list[str]) -> list[float]:
            received_queries.append(query)
            return [0.5] * len(passages)

    history_decision = HistoryDecision(
        decision="follow_up",
        contextualized_question="이전 대화 맥락 + 현재 query 합친 문장",
        confidence=0.9,
        reason="테스트",
    )
    chunks = [_chunk(chunk_id="a" * 40)]
    state = _state(query="현재 query만", candidates=chunks, history_decision=history_decision)

    cross_encoder_rerank(state, reranker=_QueryCaptureReranker())
    assert received_queries == ["이전 대화 맥락 + 현재 query 합친 문장"]


def test_falls_back_to_query_when_no_contextualized_question() -> None:
    received_queries: list[str] = []

    class _QueryCaptureReranker(CrossEncoderReranker):
        def score(self, query: str, passages: list[str]) -> list[float]:
            received_queries.append(query)
            return [0.5] * len(passages)

    chunks = [_chunk(chunk_id="a" * 40)]
    state = _state(query="원 query", candidates=chunks, history_decision=None)
    cross_encoder_rerank(state, reranker=_QueryCaptureReranker())
    assert received_queries == ["원 query"]


def test_falls_back_to_query_when_contextualized_question_empty() -> None:
    received_queries: list[str] = []

    class _QueryCaptureReranker(CrossEncoderReranker):
        def score(self, query: str, passages: list[str]) -> list[float]:
            received_queries.append(query)
            return [0.5] * len(passages)

    history_decision = HistoryDecision(
        decision="new_topic",
        contextualized_question="",  # 빈 문자열
        confidence=0.9,
    )
    chunks = [_chunk(chunk_id="a" * 40)]
    state = _state(query="원 query", candidates=chunks, history_decision=history_decision)
    cross_encoder_rerank(state, reranker=_QueryCaptureReranker())
    assert received_queries == ["원 query"]


# --- Source 매핑 ---


def test_source_field_mapping_for_page_chunk() -> None:
    chunk = _chunk(chunk_id="a" * 40, text="본문 텍스트")
    state = _state(candidates=[chunk])
    result = cross_encoder_rerank(state, reranker=_ConstantScoreReranker(score=0.75))
    src = result.sources[0]
    # docs/api-spec.md Source 스키마 정합
    assert src.title == "EKS 운영 가이드"  # page_title
    assert src.score == 75  # round(0.75 * 100)
    assert src.path == "Cloud 운영 문서 > 개요"  # section_path
    assert src.space_key == "CLOUD"
    assert src.source_type is SourceType.PAGE
    assert src.confluence_url == "/display/CLOUD/eks"  # webui_link
    assert src.text_preview == "본문 텍스트"
    # sources[].pageId — metadata.page_id 주입(2026-06-10, 코드 리뷰 A8). 누락 시
    # 기본값 "" 이 BFF 로 그대로 송신되므로 빈 문자열이 아니어야 한다.
    assert src.page_id == "P1"
    assert src.to_bff_payload()["pageId"] == "P1"
    assert src.attachment_filename is None
    assert src.attachment_mime is None
    assert src.download_url is None


def test_rerank_scores_map_populated() -> None:
    """feature17c-3: 실제 Cross-Encoder 점수가 RagState.rerank_scores 에 저장된다.

    generator 가 출처 카드 점수에 실제 rerank 점수를 반영할 수 있도록 chunk_id →
    score map 을 채운다 (top_chunks(Chunk)는 점수를 싣지 못하므로).
    """
    chunk = _chunk(chunk_id="a" * 40, text="본문 텍스트")
    state = _state(candidates=[chunk])
    result = cross_encoder_rerank(state, reranker=_ConstantScoreReranker(score=0.75))
    assert result.rerank_scores == {"a" * 40: 0.75}


def test_rerank_scores_empty_when_no_candidates() -> None:
    """검색 0건이면 rerank_scores 는 빈 dict (기본값) 으로 유지된다."""
    state = _state(candidates=[])
    result = cross_encoder_rerank(state, reranker=_ConstantScoreReranker(score=0.9))
    assert result.rerank_scores == {}


def test_source_field_mapping_for_attachment_chunk() -> None:
    chunk = _chunk(
        chunk_id="a" * 40,
        text="첨부 본문",
        is_attachment=True,
        attachment_filename="EKS_운영_매뉴얼.docx",
    )
    state = _state(candidates=[chunk])
    result = cross_encoder_rerank(state, reranker=_ConstantScoreReranker(score=0.9))
    src = result.sources[0]
    # 첨부 청크는 title이 attachment_filename
    assert src.title == "EKS_운영_매뉴얼.docx"
    assert src.source_type is SourceType.ATTACHMENT
    assert src.attachment_filename == "EKS_운영_매뉴얼.docx"
    assert src.attachment_mime == _DOCX_MIME
    # 첨부 청크도 pageId(부모 페이지)가 주입된다(코드 리뷰 A8).
    assert src.page_id == "P1"


def test_attachment_source_download_url_filled_from_lookup() -> None:
    """첨부 청크 + ChunkTextLookup 적재 → Source.download_url이 lookup 값으로 채워진다."""
    chunk_id = "a" * 40
    chunk = _chunk(
        chunk_id=chunk_id,
        is_attachment=True,
        attachment_filename="EKS_운영_매뉴얼.docx",
    )
    lookup = FakeChunkTextLookup(
        {
            chunk_id: ChunkLookupRecord(
                chunk_id=chunk_id,
                text="첨부 풀 텍스트",
                download_url="https://confluence/download/att-1",
            )
        }
    )
    state = _state(candidates=[chunk])
    result = cross_encoder_rerank(
        state, reranker=_ConstantScoreReranker(score=0.9), chunk_lookup=lookup
    )
    assert result.sources[0].download_url == "https://confluence/download/att-1"


def test_page_source_download_url_remains_none_even_with_lookup() -> None:
    """본문 청크는 lookup 조회 자체를 회피 — download_url은 항상 None."""
    chunk_id = "a" * 40
    chunk = _chunk(chunk_id=chunk_id, text="본문 텍스트")
    # 실수로 본문 청크 chunk_id에 download_url이 적재된 경우라도 정합성 보호.
    lookup = FakeChunkTextLookup(
        {
            chunk_id: ChunkLookupRecord(
                chunk_id=chunk_id,
                text="본문 풀 텍스트",
                download_url="https://confluence/should-not-leak",
            )
        }
    )
    state = _state(candidates=[chunk])
    result = cross_encoder_rerank(
        state, reranker=_ConstantScoreReranker(score=0.75), chunk_lookup=lookup
    )
    assert result.sources[0].download_url is None


def test_attachment_source_download_url_none_when_lookup_missing_record() -> None:
    """첨부 청크지만 lookup에 레코드가 없으면 download_url=None (안전 fallback)."""
    chunk = _chunk(
        chunk_id="a" * 40,
        is_attachment=True,
        attachment_filename="EKS_운영_매뉴얼.docx",
    )
    # 빈 lookup
    state = _state(candidates=[chunk])
    result = cross_encoder_rerank(
        state,
        reranker=_ConstantScoreReranker(score=0.9),
        chunk_lookup=FakeChunkTextLookup(),
    )
    assert result.sources[0].download_url is None


def test_lookup_default_none_keeps_legacy_behavior() -> None:
    """chunk_lookup=None (legacy) → 모든 Source.download_url=None."""
    chunk = _chunk(
        chunk_id="a" * 40,
        is_attachment=True,
        attachment_filename="EKS_운영_매뉴얼.docx",
    )
    state = _state(candidates=[chunk])
    result = cross_encoder_rerank(
        state, reranker=_ConstantScoreReranker(score=0.9), chunk_lookup=None
    )
    assert result.sources[0].download_url is None


def test_download_url_lookup_failure_degrades_gracefully() -> None:
    """chunk_lookup(Mongo) 조회 실패 시 쿼리를 죽이지 않고 download_url 없이 진행한다 (17c-8)."""

    class _FailingLookup(FakeChunkTextLookup):
        def fetch_many(self, chunk_ids: list[str]) -> dict[str, ChunkLookupRecord]:
            raise RuntimeError("Mongo 연결 거부 (localhost:27017)")

    chunk = _chunk(
        chunk_id="a" * 40,
        is_attachment=True,
        attachment_filename="EKS_운영_매뉴얼.docx",
    )
    state = _state(candidates=[chunk])
    # 스토리지 장애가 전파되지 않고 정상적으로 sources/top_chunks 가 채워져야 한다.
    result = cross_encoder_rerank(
        state, reranker=_ConstantScoreReranker(score=0.9), chunk_lookup=_FailingLookup()
    )
    assert len(result.top_chunks) == 1
    assert len(result.sources) == 1
    assert result.sources[0].download_url is None  # 조회 실패 → download_url 누락(graceful).


def test_score_is_rounded_to_integer_percent() -> None:
    chunk = _chunk(chunk_id="a" * 40)
    state = _state(candidates=[chunk])
    # raw 0.567 → round(56.7) = 57
    result = cross_encoder_rerank(state, reranker=_ConstantScoreReranker(score=0.567))
    assert result.sources[0].score == 57


def test_low_confidence_threshold_alignment() -> None:
    """9-A LOW_CONFIDENCE_THRESHOLD(0.55)는 Source.score 55와 정합 — 포맷터 임계 일치.

    feature17c-2: temperature scaling(T=4) 도입으로 LOW 0.20→0.55 재조정. 포맷터
    LOW_CONFIDENCE_SCORE(55)와 같은 기준(0~1 vs 0~100)으로 일치한다.
    """
    from app.query.formatter import LOW_CONFIDENCE_SCORE

    assert int(LOW_CONFIDENCE_THRESHOLD * 100) == 55
    assert int(LOW_CONFIDENCE_THRESHOLD * 100) == LOW_CONFIDENCE_SCORE


# --- chunk_id 동등성 ---


def test_top_chunks_and_sources_are_aligned() -> None:
    chunks = [
        _chunk(chunk_id="a" * 40, chunk_index=0, text="alpha"),
        _chunk(chunk_id="b" * 40, chunk_index=1, text="beta"),
        _chunk(chunk_id="c" * 40, chunk_index=2, text="gamma"),
    ]
    state = _state(candidates=chunks)
    reranker = _OrderedScoreReranker(scores=[0.4, 0.9, 0.6])
    result = cross_encoder_rerank(state, reranker=reranker)
    # top_chunks 와 sources는 같은 순서 매핑
    assert len(result.top_chunks) == len(result.sources)
    for chunk, source in zip(result.top_chunks, result.sources, strict=True):
        assert source.text_preview == chunk.text
        # title 동등성 (본문 청크)
        assert source.title == chunk.metadata.page_title


def test_returns_same_state_instance() -> None:
    """LangGraph 노드 계약 — in-place mutation."""
    state = _state(candidates=[_chunk(chunk_id="a" * 40)])
    result = cross_encoder_rerank(state, reranker=FakeCrossEncoderReranker())
    assert result is state
