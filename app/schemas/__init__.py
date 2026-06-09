"""app.schemas — 계층 간 데이터 계약 [Pipeline].

파이프라인 단계 간에 dict를 그대로 전달하지 않고 Pydantic 모델로 정의한다.
주요 모델·열거형·헬퍼를 본 패키지에서 re-export 한다.

- enums.py        DocType / AttachmentType / SourceType / ExtractedFormat / Intent /
                  VerificationStatus / IngestionStage / IngestionStatus / LlmModel
- page_object.py  PageObject, Attachment (Ingestion 입력 — 설계서 §7.1)
- chunk.py        Chunk, ChunkMetadata, make_chunk_id (chunking-strategy.md §6)
- rag_state.py    RagState, IngestionState, HistoryTurn, HistoryDecision (LangGraph 노드 상태)
- response.py     QueryResponse, Source, Verification (docs/api-spec.md)
"""

from app.schemas.chunk import Chunk, ChunkMetadata, make_chunk_id
from app.schemas.enums import (
    AttachmentType,
    DocType,
    ExtractedFormat,
    IngestionStage,
    IngestionStatus,
    Intent,
    LlmModel,
    SourceType,
    VerificationResult,
    VerificationStatus,
)
from app.schemas.page_object import Attachment, PageObject
from app.schemas.rag_state import HistoryDecision, HistoryTurn, IngestionState, RagState
from app.schemas.response import (
    QueryResponse,
    Source,
    Verification,
    VerificationSummary,
)

__all__ = [
    "Attachment",
    "AttachmentType",
    "Chunk",
    "ChunkMetadata",
    "DocType",
    "ExtractedFormat",
    "HistoryDecision",
    "HistoryTurn",
    "IngestionStage",
    "IngestionState",
    "IngestionStatus",
    "Intent",
    "LlmModel",
    "PageObject",
    "QueryResponse",
    "RagState",
    "Source",
    "SourceType",
    "Verification",
    "VerificationResult",
    "VerificationStatus",
    "VerificationSummary",
    "make_chunk_id",
]
