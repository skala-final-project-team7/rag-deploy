"""Multi-Pool Hybrid Search 핵심 로직 — RRF 융합 / Pool 가중 합산 / Top-N 선정.

--------------------------------------------------
작성자 : 최태성
작성목적 : LINA RAG 파이프라인 Query 단계의 Multi-Pool Hybrid Search 결정론적 로직을
          제공한다. Pool 내부에서 dense·sparse 순위를 RRF로 융합하고, 3개 Pool을
          가중 합산해 Top-N 후보를 선정한다 (rag-pipeline-design.md §6 4.5).
작성일 : 2026-05-15
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-15, 최초 작성, feature9-A — reciprocal_rank_fusion / merge_pools /
    select_top_candidates / fuse_and_rank (외부 의존성 없는 순수 로직)
--------------------------------------------------
[호환성]
  - Python 3.11.x
  - NOTE: 쿼리 임베딩·Qdrant 3-pool 검색·RagState 배선은 feature9-B(노드 오케스트레이션)
          책임이다. 본 모듈은 검색 결과(순위·점수)에 대한 순수 함수만 제공한다.
--------------------------------------------------
"""

# rag-pipeline-design.md §6 4.5
RRF_K = 60  # Reciprocal Rank Fusion 상수
TOP_CANDIDATES = 20  # Hybrid Search 후보 수 (재순위화 입력)


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int = RRF_K,
) -> dict[str, float]:
    """여러 순위 목록을 Reciprocal Rank Fusion으로 융합한다.

    Pool 내부에서 dense 순위와 sparse(BM25) 순위를 결합하는 데 사용한다. 각 목록에서
    item의 1-based 순위 rank에 대해 ``1 / (k + rank)``를 누적한다.

    Args:
        ranked_lists: 순위 목록들. 각 목록은 관련도 내림차순의 item id 리스트.
        k: RRF 상수. 클수록 상위 순위 간 점수 차가 완만해진다.

    Returns:
        item id → RRF 점수(높을수록 상위). 어느 목록에도 없으면 결과에 없다.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def merge_pools(
    pool_scores: dict[str, dict[str, float]],
    pool_weights: dict[str, float],
) -> dict[str, float]:
    """Pool별 {item: 점수}를 Pool 가중치로 합산한다.

    의도별 Pool 가중치(Title/Content/Label)는 질의 라우터가 RagState.pool_weights로
    전달한다 (rag-pipeline-design.md §6). 가중치 dict에 없는 Pool은 0으로 간주한다.

    Args:
        pool_scores: Pool 이름 → {item id: 점수} (보통 reciprocal_rank_fusion 결과).
        pool_weights: Pool 이름 → 가중치.

    Returns:
        item id → 가중 합산 최종 점수.
    """
    merged: dict[str, float] = {}
    for pool_name, item_scores in pool_scores.items():
        weight = pool_weights.get(pool_name, 0.0)
        for item, score in item_scores.items():
            merged[item] = merged.get(item, 0.0) + score * weight
    return merged


def select_top_candidates(
    scores: dict[str, float],
    limit: int = TOP_CANDIDATES,
) -> list[str]:
    """점수 내림차순으로 상위 limit개 item id를 반환한다.

    동점은 item id 오름차순으로 정렬해 결정론을 보장한다 (Pipeline 회귀 보호).

    Args:
        scores: item id → 점수.
        limit: 반환할 최대 item 수.

    Returns:
        점수 내림차순 상위 item id 리스트 (최대 limit개).
    """
    ordered = sorted(scores.items(), key=lambda item_score: (-item_score[1], item_score[0]))
    return [item for item, _ in ordered[:limit]]


def fuse_and_rank(
    pool_rankings: dict[str, dict[str, list[str]]],
    pool_weights: dict[str, float],
    limit: int = TOP_CANDIDATES,
) -> list[str]:
    """Multi-Pool Hybrid Search의 결정론적 결합 단계를 한 번에 수행한다.

    Pool 내부 RRF(dense+sparse) → Pool 가중 합산 → Top-N 선정 순으로 처리한다.

    Args:
        pool_rankings: Pool 이름 → {vector 종류: 순위 목록}.
            예: ``{"title_pool": {"dense": [...], "sparse": [...]}, ...}``
        pool_weights: Pool 이름 → 가중치 (질의 라우터 산출).
        limit: 반환할 최대 후보 수.

    Returns:
        최종 점수 내림차순 Top-N item id 리스트.
    """
    pool_scores = {
        pool_name: reciprocal_rank_fusion(list(vector_rankings.values()))
        for pool_name, vector_rankings in pool_rankings.items()
    }
    return select_top_candidates(merge_pools(pool_scores, pool_weights), limit)
