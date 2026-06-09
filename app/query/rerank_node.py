"""Cross-Encoder 재순위화 노드 — candidates → top_chunks + sources [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : Query 파이프라인의 재순위화 단계 LangGraph 노드. 9-B-2가 채운 RagState.
          candidates(Top-20)에 대해 9-B-1 Reranker로 (query, passage) 관련도 점수를
          산출하고, 9-A `select_reranked` 결정론 로직으로 Top-K(5 또는 3)를 선정해
          `top_chunks`와 출처 카드(`sources`)를 채운다 (`docs/rag-pipeline-design.md`
          §6 4.5·§8, `docs/api-spec.md` Source 스키마, `app/CLAUDE.md` §8).
작성일 : 2026-05-18
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-18, 최초 작성, feature9-B-3 — cross_encoder_rerank 노드 + Source 매핑
  - 2026-05-18, 풀 텍스트 lookup 후속 — ChunkTextLookup 주입으로 첨부 청크의
    Source.download_url을 채우도록 확장. lookup이 None이면 동작 무변경.
--------------------------------------------------
[호환성]
  - Python 3.11.x
  - 외부 의존성 0 (주입된 CrossEncoderReranker 구체 구현이 외부 의존성을 갖는다)
--------------------------------------------------
"""

import logging

from app.query.rerank import select_reranked
from app.query.reranker.base import CrossEncoderReranker
from app.schemas.chunk import Chunk
from app.schemas.enums import SourceType
from app.schemas.rag_state import RagState
from app.schemas.response import Source
from app.storage.chunk_lookup import ChunkTextLookup

logger = logging.getLogger(__name__)

# select_reranked.is_low_confidence는 RagState에 별도 필드로 두지 않는다. 응답 포맷터
# (feature11)의 ``_is_low_confidence`` 가 ``Source.score`` 기반으로 동일 판정하므로
# 이중 신호가 된다 — score만 정확히 매핑하면 포맷터가 자동으로 저신뢰 분기를 적용한다.

# UI 출처 카드 text_preview 폭 — chunk.text 가 feature17c-7 이후 풀 텍스트라 절단한다
# (vector_store.TEXT_PREVIEW_LIMIT / generator._preview_text 와 동일 200자).
_SOURCE_TEXT_PREVIEW_LIMIT = 200


def cross_encoder_rerank(
    state: RagState,
    *,
    reranker: CrossEncoderReranker,
    chunk_lookup: ChunkTextLookup | None = None,
) -> RagState:
    """Cross-Encoder 재순위화 LangGraph 노드 — candidates → top_chunks + sources.

    9-B-2가 채운 ``state.candidates`` (Top-20)에 대해 ``reranker.score`` 로 (query,
    passage) 관련도 점수를 산출한 뒤, 9-A ``select_reranked`` 의 결정론 선정 로직을
    적용한다. 결과를 ``state.top_chunks`` (Chunk 목록)과 ``state.sources`` (Source
    카드 목록, ``docs/api-spec.md`` 정합)에 저장한다.

    Args:
        state: ``query`` / ``candidates`` (+선택적 ``history_decision.contextualized_question``)
            를 읽고 ``top_chunks`` / ``sources`` 를 채운다.
        reranker: Cross-Encoder 어댑터. 실 운영은 ``CrossEncoderRerankerImpl`` ,
            테스트·PoC는 ``FakeCrossEncoderReranker`` 주입.
        chunk_lookup: 첨부 청크의 ``Source.download_url`` 을 채우기 위한 lookup 어댑터.
            None이면 download_url은 항상 None으로 유지 (legacy 동작 보존).

    Returns:
        ``top_chunks`` / ``sources`` 가 채워진 RagState (in-place mutation).
    """
    candidates = state.candidates

    # --- 1. 빈 candidates short-circuit ---
    # 9-B-2가 candidates를 비웠다면(검색 0건) 재순위화 무의미. top_chunks·sources도 비움.
    if not candidates:
        state.top_chunks = []
        state.sources = []
        return state

    # --- 2. 쿼리 텍스트 결정 ---
    # 멀티턴 히스토리 관리자(feature8)가 채운 contextualized_question 이 있으면 그것을,
    # 없으면 원 query를 사용한다. Cross-Encoder는 단일 query를 받으므로 rewritten_queries
    # 같은 멀티 쿼리는 지원하지 않는다 — 라우터가 contextualized_question에 압축한다.
    query_text = _query_text(state)

    # --- 3. Reranker 호출 ---
    passages = [chunk.text for chunk in candidates]
    raw_scores = reranker.score(query_text, passages)

    # --- 4. 9-A select_reranked ---
    scored_by_chunk_id = {
        chunk.metadata.chunk_id: score for chunk, score in zip(candidates, raw_scores, strict=True)
    }
    rerank_result = select_reranked(scored_by_chunk_id)

    # --- 5. top_chunks + sources 매핑 ---
    chunk_by_id = {chunk.metadata.chunk_id: chunk for chunk in candidates}
    top_chunks: list[Chunk] = []
    sources: list[Source] = []

    # 첨부 청크의 download_url을 배치 조회 — Mongo round-trip 1회로 최소화.
    download_url_by_chunk_id = _fetch_attachment_download_urls(
        chunk_ids=[chunk_id for chunk_id, _ in rerank_result.top],
        chunk_by_id=chunk_by_id,
        lookup=chunk_lookup,
    )

    for chunk_id, score in rerank_result.top:
        # rerank_result.top의 chunk_id는 모두 입력 candidates에서 나왔으므로 보장된다.
        chunk = chunk_by_id[chunk_id]
        top_chunks.append(chunk)
        sources.append(
            _chunk_to_source(
                chunk,
                raw_score=score,
                download_url=download_url_by_chunk_id.get(chunk_id),
            )
        )

    state.top_chunks = top_chunks
    state.sources = sources
    # feature17c-3: 실제 Cross-Encoder 점수를 chunk_id → score map 으로 보존한다.
    # top_chunks(Chunk)는 점수를 싣지 못하므로, 답변 생성기(generator)가 출처 카드
    # 점수에 실제 rerank 점수를 반영할 수 있도록 RagState 에 별도 저장한다.
    state.rerank_scores = {chunk_id: score for chunk_id, score in rerank_result.top}
    return state


def _fetch_attachment_download_urls(
    *,
    chunk_ids: list[str],
    chunk_by_id: dict[str, Chunk],
    lookup: ChunkTextLookup | None,
) -> dict[str, str | None]:
    """첨부 청크의 download_url 만 일괄 조회한다. lookup이 없으면 빈 dict.

    본문 청크는 lookup 대상이 아니므로 호출에서 제외한다 — Mongo 부하 최소화 + 본문
    청크에 잘못 적재된 download_url 데이터가 있어도 무시 (정합성 보호).
    """
    if lookup is None:
        return {}
    attachment_ids = [
        chunk_id
        for chunk_id in chunk_ids
        if chunk_by_id[chunk_id].metadata.source_type is SourceType.ATTACHMENT
    ]
    if not attachment_ids:
        return {}
    # download_url 은 UI 출처 카드의 부가 정보(첨부 다운로드 링크)다. chunk_lookup
    # (Mongo) 일시 장애가 RAG 쿼리 전체를 실패시키면 안 되므로, 조회 실패 시 download_url
    # 없이 graceful degrade 한다 (feature17c-8 — 첨부 청크는 payload 풀텍스트로 이미
    # 검색·생성 가능하며 download_url 만 누락). 본문 검색·생성 품질에는 영향 없다.
    try:
        records = lookup.fetch_many(attachment_ids)
    except Exception as exc:  # noqa: BLE001 — 스토리지 장애를 쿼리 실패로 전파하지 않는다.
        logger.warning("chunk_lookup download_url 조회 실패 — download_url 없이 진행: %s", exc)
        return {}
    return {chunk_id: record.download_url for chunk_id, record in records.items()}


def _query_text(state: RagState) -> str:
    """contextualized_question 우선, 없으면 원 query."""
    if state.history_decision and state.history_decision.contextualized_question:
        return state.history_decision.contextualized_question
    return state.query


def _chunk_to_source(
    chunk: Chunk,
    *,
    raw_score: float,
    download_url: str | None = None,
) -> Source:
    """Chunk + Cross-Encoder raw score → Source 출처 카드 (docs/api-spec.md).

    raw score는 어댑터 측에서 ``[0.0, 1.0]`` 으로 정규화된 상태(9-B-1 Sigmoid)다. Source.
    score는 ``int 0~100`` 스케일이므로 ``round(raw_score * 100)`` 로 변환한다 — 포맷터
    (feature11)의 ``LOW_CONFIDENCE_SCORE`` 임계값(20)과 정합.

    ``download_url`` 은 호출자(cross_encoder_rerank)가 ChunkTextLookup 으로 첨부 청크
    에 대해 배치 조회해 주입한다. 본문 청크는 None.
    """
    metadata = chunk.metadata
    # 첨부 청크는 출처 카드 제목을 attachment_filename으로, 본문 청크는 page_title로.
    title = metadata.attachment_filename or metadata.page_title
    # chunk.text 는 feature17c-7 이후 풀 텍스트이므로 UI 출처 카드용 미리보기는 200자로
    # 절단한다 (payload text_preview / generator _preview_text 와 동일 폭).
    return Source(
        title=title,
        score=round(raw_score * 100),
        path=metadata.section_path,
        space_key=metadata.space_key,
        source_type=metadata.source_type,
        confluence_url=metadata.webui_link,
        last_modified=metadata.last_modified,
        text_preview=chunk.text[:_SOURCE_TEXT_PREVIEW_LIMIT],
        attachment_filename=metadata.attachment_filename,
        attachment_mime=metadata.attachment_mime,
        download_url=download_url,
    )
