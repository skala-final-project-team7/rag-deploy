"""Multi-Pool Hybrid Search 노드 — query 임베딩 + 3 Pool dense+sparse + RRF [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : Query 파이프라인의 검색 단계 LangGraph 노드. RagState의 query (+ 선택적
          rewritten_queries)를 받아 dense·sparse 임베딩(5-B-1) → 3 Pool ACL 필터 검색
          (5-B-2) → 9-A 결정론 로직(RRF + Pool 가중 합산 + Top-N 선정) → Chunk 재구성
          순으로 처리해 ``RagState.candidates`` Top-20을 채운다
          (`docs/rag-pipeline-design.md` §6 4.5, `app/CLAUDE.md` §3·§8, db-schema.md §1.2).
          ``@enforce_acl`` 가드(feature7)로 ACL 미주입 호출을 시스템 단에서 거부한다.
작성일 : 2026-05-18
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-18, 최초 작성, feature9-B-2 — hybrid_search 외부 노드 + 내부 ACL 가드
    + Chunk 재구성 + 기본 pool_weights fallback
  - 2026-05-18, 5-A 후속 — _chunk_from_search_hit가 payload.token_count를 그대로
    복원하도록 변경 (build_point_payload 동봉 확장과 짝). legacy 인덱스 호환 위해
    필드 없으면 0 fallback.
--------------------------------------------------
[호환성]
  - Python 3.11.x
  - 외부 의존성 0 (주입된 DenseEmbedder / SparseEmbedder / QdrantPoolStore의 구체
    구현이 외부 의존성을 갖는다)
--------------------------------------------------
"""

from datetime import datetime
from typing import Any

from app.ingestion.embedder.base import DenseEmbedder, SparseEmbedder
from app.ingestion.vector_store import POOL_NAMES
from app.query.acl import enforce_acl
from app.query.search import TOP_CANDIDATES, fuse_and_rank
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import AttachmentType, DocType, ExtractedFormat, SourceType
from app.schemas.rag_state import RagState
from app.storage.qdrant_client import QdrantPoolStore, SearchHit

# 라우터(feature8)가 pool_weights를 채우지 않은 경우의 안전한 fallback. 등가 가중치 —
# 라우터의 intent 추정이 동작하면 즉시 덮어쓴다.
_DEFAULT_POOL_WEIGHTS: dict[str, float] = dict.fromkeys(POOL_NAMES, 1.0)


def hybrid_search(
    state: RagState,
    *,
    dense_embedder: DenseEmbedder,
    sparse_embedder: SparseEmbedder,
    store: QdrantPoolStore,
    top_k: int = TOP_CANDIDATES,
) -> RagState:
    """Multi-Pool Hybrid Search LangGraph 노드.

    ``(state) -> state`` 표준 시그니처. 외부 의존성은 키워드 인자로 주입한다 — LangGraph
    그래프 조립(feature11)에서 ``functools.partial`` 또는 클로저로 wiring한다.
    ACL 미주입 호출은 내부 ``@enforce_acl`` 가드로 ``ACLViolationError`` 발생.

    Args:
        state: ``query`` / 선택적 ``rewritten_queries`` / ``acl_filter`` /
            ``pool_weights`` / ``metadata_filters`` 를 읽고 ``candidates`` 를 채운다.
        dense_embedder: query 텍스트를 dense 벡터로 변환.
        sparse_embedder: query 텍스트를 sparse 벡터로 변환.
        store: Qdrant Multi-Pool 저장소.
        top_k: 반환할 후보 수. 기본값 ``TOP_CANDIDATES=20``.

    Returns:
        ``candidates`` 가 채워진 RagState (입력 state를 갱신해 반환).

    Raises:
        ACLViolationError: ``state.acl_filter`` 가 무효일 때.
    """
    return _hybrid_search_acl_guarded(
        state,
        acl_filter=state.acl_filter,
        dense_embedder=dense_embedder,
        sparse_embedder=sparse_embedder,
        store=store,
        top_k=top_k,
    )


@enforce_acl
def _hybrid_search_acl_guarded(
    state: RagState,
    *,
    acl_filter: dict[str, Any] | None,
    dense_embedder: DenseEmbedder,
    sparse_embedder: SparseEmbedder,
    store: QdrantPoolStore,
    top_k: int,
) -> RagState:
    """ACL 가드를 통과한 본 검색 로직 — 외부 호출은 ``hybrid_search`` 통해서만."""
    # @enforce_acl이 acl_filter 유효성을 검증했으므로 여기서는 사용 측에서 None이 아님이
    # 보장된다. mypy 안전을 위해 명시 단언.
    assert acl_filter is not None

    # --- 1. 쿼리 텍스트 결정 ---
    # 라우터가 rewritten_queries를 채웠으면 그것들로, 아니면 원 query 단일 사용.
    query_texts = list(state.rewritten_queries) if state.rewritten_queries else [state.query]

    # --- 2. query 배치 임베딩 (dense + sparse 한 번씩) ---
    dense_query_vectors = dense_embedder.encode_queries(query_texts)
    sparse_query_vectors = sparse_embedder.encode_queries(query_texts)

    # --- 3. Pool 검색 + 결합 ---
    pool_weights = state.pool_weights or _DEFAULT_POOL_WEIGHTS
    # 검색 메타데이터 필터는 라우터(LLM 질의 이해)가 추정한 ``metadata_filters`` 에서만 온다.
    # 요청 본문에는 더 이상 ``spaceKey`` 가 없으므로(명세 정합 — 2026-06-04), 별도의 요청
    # 스페이스 하드 스코프는 두지 않는다. ``_coerce_metadata_filters`` 가 라우터 복수형 키
    # (space_keys/document_types/source_types)를 Qdrant payload 단수형(space_key/doc_type/
    # source_type)으로 정규화한다.
    metadata_filters = _coerce_metadata_filters(state.metadata_filters)

    candidates = _search_and_fuse(
        store=store,
        acl_filter=acl_filter,
        query_texts=query_texts,
        dense_query_vectors=dense_query_vectors,
        sparse_query_vectors=sparse_query_vectors,
        pool_weights=pool_weights,
        top_k=top_k,
        metadata_filters=metadata_filters,
    )

    # --- 4. metadata filter 0건 fallback (feature17c-6) ---
    # 라우터가 추출한 metadata filter 는 LLM 추정값이라 payload(doc_type/source_type 등)와
    # 불일치할 수 있다. 불일치 시 must 결합으로 전체 검색이 0건이 되어(query_points 에 score
    # 하한이 없어 필터 통과 포인트가 없으면 곧 0건) 첨부·특정 유형 명시 질의가 통째로 검색
    # 실패하던 문제(EVAL-021/024/044/046)를 방지한다. 0건이면 metadata filter 를 완전히
    # 완화(None)해 ACL 만 유지한 채 1회 재검색한다(권한 경계는 완화하지 않음). 필터가 애초에
    # 없었으면 완화할 대상이 없으므로 재검색하지 않는다.
    if not candidates and metadata_filters:
        candidates = _search_and_fuse(
            store=store,
            acl_filter=acl_filter,
            query_texts=query_texts,
            dense_query_vectors=dense_query_vectors,
            sparse_query_vectors=sparse_query_vectors,
            pool_weights=pool_weights,
            top_k=top_k,
            metadata_filters=None,
        )

    state.candidates = candidates
    return state


def _search_and_fuse(
    *,
    store: QdrantPoolStore,
    acl_filter: dict[str, Any],
    query_texts: list[str],
    dense_query_vectors: list[list[float]],
    sparse_query_vectors: list[Any],
    pool_weights: dict[str, float],
    top_k: int,
    metadata_filters: dict[str, str | list[str]] | None,
) -> list[Chunk]:
    """3 Pool × N query × {dense, sparse} 검색 → 9-A RRF 결합 → Chunk 재구성.

    ``hybrid_search`` 본체에서 분리해 metadata filter 유무로 동일 검색을 재실행할 수
    있게 한다(feature17c-6 fallback). 임베딩은 호출자가 1회만 계산해 주입한다.

    Args:
        store: Qdrant Multi-Pool 저장소.
        acl_filter: ``build_acl_filter`` 출력 dict (모든 검색에 must 결합).
        query_texts: 원/확장 쿼리 텍스트(임베딩 인덱스와 1:1).
        dense_query_vectors: query별 dense 벡터.
        sparse_query_vectors: query별 sparse 벡터.
        pool_weights: Pool 가중치(라우터 또는 fallback).
        top_k: Pool별·최종 상위 N.
        metadata_filters: payload 필드 match 필터(None 이면 ACL 만 적용).

    Returns:
        RRF 결합 후 Top-N ``Chunk`` 목록(검색 0건이면 빈 list).
    """
    pool_rankings: dict[str, dict[str, list[str]]] = {pool: {} for pool in POOL_NAMES}
    all_hits: dict[str, SearchHit] = {}

    for pool_name in POOL_NAMES:
        for idx, _ in enumerate(query_texts):
            dense_hits = store.search(
                pool_name,
                acl_filter=acl_filter,
                dense_vector=dense_query_vectors[idx],
                top_k=top_k,
                metadata_filters=metadata_filters,
            )
            sparse_hits = store.search(
                pool_name,
                acl_filter=acl_filter,
                sparse_vector=sparse_query_vectors[idx],
                top_k=top_k,
                metadata_filters=metadata_filters,
            )
            # 9-A `fuse_and_rank` 입력은 vector_type 키 단위로 묶인 ranking. query별로
            # 키를 분리해 RRF가 모든 ranking을 동등하게 합치도록 한다.
            pool_rankings[pool_name][f"dense_q{idx}"] = [hit.chunk_id for hit in dense_hits]
            pool_rankings[pool_name][f"sparse_q{idx}"] = [hit.chunk_id for hit in sparse_hits]
            # Chunk 재구성용 SearchHit 풀 — 같은 chunk_id는 payload가 동일하므로 덮어써도 안전.
            for hit in (*dense_hits, *sparse_hits):
                all_hits[hit.chunk_id] = hit

    top_chunk_ids = fuse_and_rank(pool_rankings, pool_weights, limit=top_k)
    return [
        _chunk_from_search_hit(all_hits[chunk_id])
        for chunk_id in top_chunk_ids
        if chunk_id in all_hits
    ]


# 라우터(`query_routing_agent` MetadataFilter.to_dict) 가 emit 하는 복수형 키 →
# Qdrant payload 인덱스 필드명(qdrant_client._KEYWORD_INDEX_FIELDS, db-schema §1.3).
# vendoring 라우터는 무수정 보존하므로 본 어댑터에서 키를 정합한다. 매핑에 없는 키는
# 그대로 통과시킨다(payload 필드명을 직접 전달하는 경우 후방 호환). ``labels`` 는 양쪽
# 동일하므로 매핑 불필요.
_ROUTER_PLURAL_TO_PAYLOAD_KEY: dict[str, str] = {
    "space_keys": "space_key",
    "document_types": "doc_type",
    "source_types": "source_type",
}


def _coerce_metadata_filters(
    metadata_filters: dict[str, Any] | None,
) -> dict[str, str | list[str]] | None:
    """RagState.metadata_filters(dict[str, Any]) → QdrantPoolStore.search 시그니처 정합.

    QdrantPoolStore는 ``str | list[str]`` 만 받는다(MatchValue/MatchAny). 라우터가 채운
    값이 그 두 타입이 아니면 None으로 떨어뜨려 무시한다 — 잘못된 값으로 검색이 망가지는
    것보다 필터 미적용이 안전하다. 이로써 ``date_range``(dict) / ``attachment_required``
    (bool) 처럼 단순 match 대상이 아닌 항목은 자연히 거른다.

    또한 라우터 ``MetadataFilter.to_dict`` 는 ``space_keys`` / ``document_types`` /
    ``source_types`` 처럼 **복수형 키** 를 emit 하는데 Qdrant payload 인덱스 필드는
    ``space_key`` / ``doc_type`` / ``source_type`` 처럼 **단수형** 이다. 키가 그대로
    통과하면 존재하지 않는 payload 필드로 must 필터가 만들어져 검색이 0건이 된다
    (2026-05-20 feature17c-5 — 첨부/공간 명시 질의가 검색 0건이 되던 2차 원인 수정).
    ``_ROUTER_PLURAL_TO_PAYLOAD_KEY`` 로 복수형 키만 payload 필드명으로 rename 하고,
    그 외 키(이미 단수형인 payload 필드명 직접 전달 포함)는 그대로 통과시킨다.

    빈 list (``[]``) / 빈 문자열 (``""``) 은 명시적으로 거른다 — Qdrant ``MatchAny
    (any=[])`` 는 어떤 값과도 매칭되지 않아 must 결합 시 모든 결과를 차단한다 (2026-
    05-20 라우터의 빈 배열 metadata_filters 가 검색 0건을 일관 유발하던 버그 수정).
    """
    if not metadata_filters:
        return None
    coerced: dict[str, str | list[str]] = {}
    for raw_key, value in metadata_filters.items():
        # 복수형 라우터 키는 payload 단수형으로 rename, 그 외는 그대로 통과.
        payload_key = _ROUTER_PLURAL_TO_PAYLOAD_KEY.get(raw_key, raw_key)
        if isinstance(value, str):
            if value:  # 빈 문자열 거름.
                coerced[payload_key] = value
        elif isinstance(value, list):
            # 빈 list 거름 + 모든 원소가 str 일 때만 받음.
            if value and all(isinstance(item, str) for item in value):
                coerced[payload_key] = value
    return coerced or None


def _chunk_from_search_hit(hit: SearchHit) -> Chunk:
    """SearchHit.payload(db-schema §1.2) → Chunk 도메인 객체 재구성.

    Cross-Encoder reranker(9-B-3) / 답변 생성기 / 응답 포맷터가 ``Chunk`` 모양을 요구하므로
    검색 단계에서 변환한다. ``text`` 는 payload의 풀 텍스트 ``text`` 를 사용한다
    (feature17c-7) — 재순위화·답변 생성이 200자 프리뷰가 아닌 풀 텍스트로 동작해야
    정답이 200자 뒤에 있는 청크가 rerank 탈락·생성기 거부되지 않는다. legacy 인덱스에
    ``text`` 필드가 없으면 ``text_preview`` (첫 200자)로 fallback (후방 호환).
    ``token_count`` 는 5-A 후속(2026-05-18)에서 payload에 동봉했으므로 payload에서
    직접 복원한다. legacy 인덱스에 필드가 없으면 0으로 fallback (후방 호환).
    """
    payload = hit.payload
    metadata = ChunkMetadata(
        chunk_id=str(payload["chunk_id"]),
        page_id=str(payload["page_id"]),
        page_title=str(payload["page_title"]),
        section_header=str(payload["section_header"]),
        section_path=str(payload["section_path"]),
        chunk_index=int(payload["chunk_index"]),
        labels=list(payload.get("labels") or []),
        doc_type=_parse_doc_type(payload["doc_type"]),
        space_key=str(payload["space_key"]),
        allowed_groups=list(payload.get("allowed_groups") or []),
        allowed_users=list(payload.get("allowed_users") or []),
        webui_link=str(payload["webui_link"]),
        last_modified=datetime.fromisoformat(str(payload["last_modified"])),
        source_type=SourceType(payload["source_type"]),
        attachment_id=_optional_str(payload.get("attachment_id")),
        attachment_filename=_optional_str(payload.get("attachment_filename")),
        attachment_mime=_optional_str(payload.get("attachment_mime")),
        extracted_format=_parse_extracted_format(payload.get("extracted_format")),
        token_count=int(payload.get("token_count") or 0),
    )
    # 풀 텍스트(text) 우선, 없으면 legacy 인덱스 호환으로 text_preview(200자) fallback.
    text = str(payload.get("text") or payload.get("text_preview") or "")
    return Chunk(text=text, metadata=metadata)


def _parse_doc_type(value: object) -> DocType | AttachmentType:
    """db-schema §1.2의 doc_type 문자열을 DocType 또는 AttachmentType로 환원한다."""
    text = str(value)
    # StrEnum 값을 직접 매칭 시도 — 본문 6유형 우선, 실패하면 첨부 4유형으로 시도.
    try:
        return DocType(text)
    except ValueError:
        return AttachmentType(text)


def _parse_extracted_format(value: object) -> ExtractedFormat | None:
    if value is None or value == "":
        return None
    return ExtractedFormat(str(value))


def _optional_str(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
