"""RagState / IngestionState — LangGraph 노드 상태 스키마 검증."""

from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import IngestionStage, Intent
from app.schemas.page_object import PageObject
from app.schemas.rag_state import HistoryTurn, IngestionState, RagState

_PAGE = PageObject(
    page_id="CONF-PAGE-1",
    space_key="INFRA",
    title="EKS 노드 조인 실패",
    body_html="<p>kubelet 로그 확인</p>",
    version_number=1,
    last_modified="2026-05-01T00:00:00+09:00",
    allowed_groups=["sre-team"],
    allowed_users=[],
    webui_link="/display/INFRA/eks",
)


def test_rag_state_minimal_input() -> None:
    state = RagState(query="S3 권한 오류 어떻게 풀었어?", user_id="user_123")
    # 입력만으로 생성 가능, 이후 단계 필드는 기본값
    assert state.needs_search is True
    assert state.groups == []
    assert state.intent is None
    assert state.candidates == []
    assert state.top_chunks == []
    assert state.answer is None


def test_history_turn_role_normalized_to_lowercase() -> None:
    """api-spec v2.4.0 §2-1 — history[].role 은 lowercase(user/assistant)로 정규화한다.

    명세 예시는 소문자이며 boundary 변환이 없다. 대소문자 무관 입력을 수용하되 표준 소문자로
    저장한다(멀티턴 히스토리 관리자도 내부적으로 소문자화).
    """
    assert HistoryTurn(role="user", content="q").role == "user"
    assert HistoryTurn(role="assistant", content="a").role == "assistant"
    # 대소문자 무관 수용 — 대문자/혼합 입력도 소문자로 정규화.
    assert HistoryTurn(role="USER", content="q").role == "user"
    assert HistoryTurn(role="Assistant", content="a").role == "assistant"
    # content 는 보존.
    turn = HistoryTurn(role="user", content="S3 관련 장애 이력 알려줘")
    assert turn.content == "S3 관련 장애 이력 알려줘"


def test_rag_state_progressive_population() -> None:
    state = RagState(query="질문", user_id="user_123", groups=["sre-team"])
    # 그래프가 진행되며 필드가 채워지는 시나리오
    state.intent = Intent.INCIDENT_RESPONSE
    state.rewritten_queries = ["S3 AccessDenied 해결", "IAM 정책 복구"]
    state.pool_weights = {"title": 0.4, "content": 0.5, "label": 0.1}
    assert state.intent is Intent.INCIDENT_RESPONSE
    assert len(state.rewritten_queries) == 2
    assert state.pool_weights["content"] == 0.5


def test_ingestion_state_holds_page_and_chunks() -> None:
    meta = ChunkMetadata(
        chunk_id="c1",
        page_id="CONF-PAGE-1",
        page_title="EKS 노드 조인 실패",
        section_header="증상",
        section_path="INFRA > EKS 노드 조인 실패 > 증상",
        chunk_index=0,
        doc_type="troubleshoot",
        space_key="INFRA",
        allowed_groups=["sre-team"],
        allowed_users=[],
        webui_link="/display/INFRA/eks#증상",
        last_modified="2026-05-01T00:00:00+09:00",
        source_type="page",
        token_count=120,
    )
    state = IngestionState(page=_PAGE, chunks=[Chunk(text="증상...", metadata=meta)])
    state.stage = IngestionStage.CHUNK
    assert state.page.page_id == "CONF-PAGE-1"
    assert len(state.chunks) == 1
    assert state.stage is IngestionStage.CHUNK
    assert state.status is None  # 아직 미설정
