"""RAG 파이프라인 로컬 실행 데모 — 팀원 시연용.

samples/*.json(Atlassian 응답 포맷)을 JsonFixtureSourceAdapter로 읽어 표준 PageObject로
변환(데이터 계층)하고, Adaptive Chunker로 청크 분할(청킹 계층)하는 것을 콘솔에 요약 출력한다.
현재까지 구현된 feature1(스키마)·feature2 일부(어댑터)·feature3(본문 청킹)을 실제 데이터로 보여준다.

--------------------------------------------------
실행 방법 (저장소 루트에서):

    # 1) Python 3.11 가상환경 (프로젝트 요구: Python 3.11.x)
    python3.11 -m venv .venv
    source .venv/bin/activate            # Windows: .venv\\Scripts\\activate

    # 2) 현재 단계 시연에 필요한 의존성 설치
    pip install pydantic pydantic-settings beautifulsoup4

    # 3) 데모 실행
    python -m examples.demo_data_layer

(전체 의존성 설치는 `pip install -e ".[dev]"` — 단, langgraph/qdrant 등 무거운
 패키지가 함께 설치되므로 현재 단계 시연만이라면 위 설치로 충분하다.)
--------------------------------------------------
"""

from collections import Counter
from pathlib import Path

from app.adapters.json_fixture import JsonFixtureSourceAdapter
from app.ingestion.chunker import chunk_page, infer_doc_type

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SAMPLES_DIR = _REPO_ROOT / "samples"


def main() -> None:
    """samples/ 전체를 PageObject로 로드하고 청크 분할까지 요약을 출력한다."""
    print("=" * 60)
    print("  LINA RAG Pipeline — 데이터 계층 + 청킹 데모")
    print("=" * 60)
    print(f"샘플 경로: {_SAMPLES_DIR}")

    adapter = JsonFixtureSourceAdapter(samples_dir=_SAMPLES_DIR)
    pages = list(adapter.fetch_pages())

    print(f"\n[로드] PageObject {len(pages)}개 — Pydantic 스키마 검증 통과 (오류 0건)")

    by_space = Counter(page.space_key for page in pages)
    print("\n[스페이스 분포]")
    for space_key, count in sorted(by_space.items()):
        print(f"  {space_key:<12} {count:>3}개")

    acl_missing = sum(1 for page in pages if page.is_acl_missing)
    print(f"\n[ACL] 누락(is_acl_missing) {acl_missing}개 / 전체 {len(pages)}개")
    print("  └ PoC: JsonFixtureSourceAdapter가 space_key 기반으로 ACL을 합성")

    attachments = [(p.page_id, a) for p in pages for a in p.attachments]
    print(f"\n[첨부] {len(attachments)}건")
    for page_id, attachment in attachments:
        print(f"  - page {page_id}: {attachment.filename} [{attachment.extracted_format}]")

    active = adapter.list_active_ids()
    print(
        f"\n[Reconciliation] active pages {len(active.pages)} / "
        f"attachments {len(active.attachments)}"
    )

    sample = next(p for p in pages if p.page_id == "100001")
    print("\n[샘플 페이지]")
    print(f"  page_id       : {sample.page_id}")
    print(f"  title         : {sample.title}")
    print(f"  space_key     : {sample.space_key}")
    print(f"  version       : {sample.version_number}")
    print(f"  last_modified : {sample.last_modified.isoformat()}")
    print(f"  labels        : {sample.labels}")
    print(f"  ancestors     : {sample.ancestors}")
    print(f"  allowed_groups: {sample.allowed_groups}")
    print(f"  attachments   : {[a.filename for a in sample.attachments]}")

    # --- 청킹 계층 (feature3: Adaptive Chunker 본문) ---
    doc_type_dist: Counter[str] = Counter()
    total_chunks = 0
    for page in pages:
        doc_type_dist[str(infer_doc_type(page))] += 1
        total_chunks += len(chunk_page(page))
    print(f"\n[청킹] {len(pages)}개 페이지 → {total_chunks}개 청크 (오류 0건)")
    print("  doc_type 추정(라벨 휴리스틱, 실제는 문서 분석기 Agent 담당):")
    for doc_type, count in sorted(doc_type_dist.items()):
        print(f"    {doc_type:<14} {count:>3}개")

    sample_chunks = chunk_page(sample)
    print(f"\n[샘플 페이지 청킹] '{sample.title}' → {len(sample_chunks)}개 청크")
    for chunk in sample_chunks:
        meta = chunk.metadata
        preview = chunk.text[:60].replace("\n", " ")
        print(f"  #{meta.chunk_index} [{meta.section_header}] {meta.token_count}토큰  {preview}...")

    print("\n" + "=" * 60)
    print(f"  ✓ 데이터 계층 + 청킹 정상 — {len(pages)}개 페이지 → {total_chunks}개 청크")
    print("=" * 60)


if __name__ == "__main__":
    main()
