"""LangGraph 노드 상태 — RagState / IngestionState.

--------------------------------------------------
작성자 : 최태성
작성목적 : Query / Ingestion LangGraph 그래프의 노드 간 전달 상태를 정의한다.
          각 노드가 단계별로 필드를 채워 나가는 상태 봉투(envelope) 역할.
작성일 : 2026-05-15
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-15, 최초 작성, feature1 schemas — Query/Ingestion 상태 정의
  - 2026-05-15, feature8 통합, HistoryDecision 모델 + RagState.history_decision 추가
    (vendoring한 history-manager-agent의 히스토리 판단 출력을 RagState로 전달)
--------------------------------------------------
[호환성]
  - Python 3.11.x, Pydantic 2.7+
--------------------------------------------------
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.chunk import Chunk
from app.schemas.enums import IngestionStage, IngestionStatus, Intent, LlmModel
from app.schemas.page_object import PageObject
from app.schemas.response import Source, Verification


class HistoryTurn(BaseModel):
    """멀티턴 대화 1턴.

    ``role`` 값은 api-spec v2.4.0 §2-1 / "Enum 값 표기 정책"에 따라 ``user`` / ``assistant``
    **lowercase** 다 — LLM/OpenAI 산업 표준(Enum 정책의 명시된 예외). 저장(`docs/db-schema.md`
    `messages.role`)·외부 응답(§1-2)·RAG 와이어(`/ml/query` `history[].role`)가 모두 lowercase
    로 통일되며 **boundary 변환이 없다**. 대소문자 무관 입력을 수용하되 표준 소문자로 정규화한다
    (하위 호환 — 구버전이 대문자로 보내도 받는다). 멀티턴 히스토리 관리자 agent 도 입력
    role 을 내부적으로 소문자화한다(`history_manager_agent/history/normalization.py`).
    """

    role: str  # "user" | "assistant" (정규화 후)
    content: str

    @field_validator("role")
    @classmethod
    def _normalize_role(cls, value: str) -> str:
        """role 을 api-spec Enum 정책의 lowercase 표기로 정규화한다(대소문자 무관 수용)."""
        return value.strip().lower()


class HistoryDecision(BaseModel):
    """멀티턴 히스토리 관리자(History Manager Agent)의 히스토리 판단 결과.

    ``history_manager_agent`` 패키지(ai-agent 소유)가 산출한 판단을 RagState로 전달하는
    어댑터 모델이다. ``app/query/history.py``의 ``manage_history`` 노드가 채운다.
    """

    decision: str  # follow_up | new_topic | ambiguous (unknown-safe 문자열)
    contextualized_question: str
    preserved_context: dict[str, Any] = Field(default_factory=dict)  # summary/entities/turn_refs
    reset_required: bool = False
    confidence: float = 0.0
    reason: str = ""
    warnings: list[str] = Field(default_factory=list)


class RagState(BaseModel):
    """Query 파이프라인 LangGraph 상태. 단계가 진행되며 필드가 채워진다."""

    # 입력
    query: str
    user_id: str
    conversation_id: str | None = None
    groups: list[str] = Field(default_factory=list)
    # ACL Pre-filtering (§4.2)
    acl_filter: dict[str, Any] | None = None
    # 멀티턴 히스토리 (§4.3)
    history: list[HistoryTurn] = Field(default_factory=list)
    needs_search: bool = True
    history_decision: HistoryDecision | None = None  # 멀티턴 히스토리 관리자 판단 결과
    # 질의 라우터 (§4.4)
    intent: Intent | None = None
    rewritten_queries: list[str] = Field(default_factory=list)
    metadata_filters: dict[str, Any] | None = None
    pool_weights: dict[str, float] | None = None
    target_llm: LlmModel | None = None
    # 검색·재순위화 (§4.5)
    candidates: list[Chunk] = Field(default_factory=list)  # Hybrid Search Top-20
    top_chunks: list[Chunk] = Field(default_factory=list)  # Cross-Encoder Top-5
    # Cross-Encoder 재순위화 점수 map (chunk_id → score 0~1). feature17c-3 (2026-05-20):
    # top_chunks(Chunk)는 점수를 싣지 못하므로, rerank_node 가 select_reranked 결과의
    # 실제 Cross-Encoder 점수를 본 map 에 저장한다. 답변 생성기(generator)가 이 map 을
    # 읽어 출처 카드 점수(Source.score)에 실제 rerank 점수를 반영한다 — map 이 비어 있으면
    # generator 는 순서 보존용 fallback 값을 쓴다(후방 호환).
    rerank_scores: dict[str, float] = Field(default_factory=dict)
    # 답변 생성·검증·포맷 (§4.6~4.8)
    answer: str | None = None
    sources: list[Source] = Field(default_factory=list)
    verification: list[Verification] = Field(default_factory=list)
    used_llm: LlmModel | None = None
    latency_ms: int | None = None


class IngestionState(BaseModel):
    """Ingestion 파이프라인 LangGraph 상태."""

    page: PageObject
    doc_type: str | None = None  # 문서 분석기 결과(본문) / attachment_type(첨부)
    chunks: list[Chunk] = Field(default_factory=list)
    stage: IngestionStage | None = None
    status: IngestionStatus | None = None
    error: str | None = None
