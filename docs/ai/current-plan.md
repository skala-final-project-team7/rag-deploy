# Current Plan

이 문서는 현재 진행 중인 작업의 Plan을 기록한다. 구현 전에 작성하고, 작업 중 계획이 바뀌면 함께 수정한다.
하나의 feature가 끝나면 체크 처리하고, 모든 feature가 끝나면 새 세션에서 다음 Plan을 작성한다.

> **상태: 제안 초안.** 설계 문서(`docs/rag-pipeline-design.md`, `docs/chunking-strategy.md`)를 기반으로
> 작성한 feature 분해 초안이다. 본격 착수 시 Claude Code Plan Mode로 feature별 상세 Plan을
> 다시 확정한다(`docs/ai/workflow.md` §2). 아래 순서·범위는 팀 리뷰 후 조정한다.

---

## 작업 개요

- **작업 목표**: RAG Pipeline 기본 골격 구축 — Ingestion·Query 양 파이프라인의 동작 가능한 MVP
- **담당 영역**: RAG Pipeline (`app/`, `tests/`)
- **브랜치 규칙**: feature별로 `feat/#<이슈번호>/<기능-이름>`
- **수정 가능 파일**: `app/`, `tests/`, 관련 `docs/`
- **수정 금지 파일**: 루트 `CLAUDE.md`, `docs/ai/workflow.md`·`prompt-templates.md`, 다른 팀원 담당 영역
- **참고 문서**: 루트 `CLAUDE.md`, `app/CLAUDE.md`, `docs/rag-pipeline-design.md`, `docs/chunking-strategy.md`, `docs/db-schema.md`, `docs/api-spec.md`, `docs/atlassian-api.md`, `docs/conventions.md`

## 선행 확인 / 의존성

- [x] **기획서 `PLAN-CONF-RAG-2026-002 v2.1.6`** — 확보 완료. 설계서와 정합성 확인됨
- [x] **첨부 파일 원본 4건** — 확보 완료, `samples/attachments/`에 위치 (feature4 픽스처)
- [x] **샘플 데이터** — `samples/`에 confluence(57p)·datadog(35p) JSON 배치 완료
- [x] **Atlassian API 명세** — 확보. `docs/atlassian-api.md`로 정리. 데이터 수집은 ML 파이프라인(본 저장소) 책임
- [x] **ACL 필드 모델 결정** — `allowed_groups`/`allowed_users` 청크 Payload 모델 채택(기획서 §6.6·설계서 원안). `app/query/acl.py`의 필터 생성 로직은 추후 교체 가능하도록 분리. → 결정 완료

### 미정 (TBD) — 기록 후 후속 단계에서 해소

- [x] **ACL 모델 — 결정됨 (api-spec v2.4/v2.5, ADR 0003 항목 1)**: 운영은 page-level `allowed_groups`/`allowed_users` **채택**(Admin Key read restriction + 빈 권한 시 공개 sentinel `"*"`). `space:{key}` 합성(`JsonFixtureSourceAdapter._synthesize_acl` / ingestion `synthesize_space_acl`)은 **PoC fixture / Admin-Key-OFF fallback**이며, rag `build_acl_filter`(검색 seam)가 `"*"`를 주입해 양쪽을 매칭한다. `docs/adr/0003-ingestion-rag-shared-contracts.md` 참조
- [x] **`access_token`/`cloudid` 전달 경로 — ingestion↔rag 합의 불필요(Auth/BFF 소관, ADR 0003)**: Authorization Server(Spring)→BFF→ML 전달 방식은 두 ML 레포 간 결정 대상이 아니다. RAG 코어 코드는 이 결정과 무관하게 선행 진행(rag는 JWT 발급/검증 없이 추출만)
- [ ] **PageObject 계약 동결** — `attachments[]` 등 스펙 동결 (`docs/rag-pipeline-design.md` §7.1)

---

## Milestone A — 공통 기반

### feature1: schemas + config  ✅ 완료 (2026-05-15, 35 tests passed)

- **작업 목표**: 파이프라인 전 단계가 공유하는 Pydantic 데이터 계약과 환경 설정 정의
- **브랜치**: `feat/#1/rag-pipeline-skeleton` (골격과 동일 change-set 연장 — 기반 작업)
- **수정 대상 파일**:
  - `app/schemas/enums.py` — DocType(6) / AttachmentType(4) / SourceType / ExtractedFormat / Intent(4) / VerificationStatus / IngestionStage / IngestionStatus / LlmModel
  - `app/schemas/page_object.py` — `PageObject`, `Attachment` (Ingestion 입력, 설계서 §7.1)
  - `app/schemas/chunk.py` — `Chunk`, `ChunkMetadata` (19종, chunking-strategy §6) + `make_chunk_id()` 결정론 헬퍼
  - `app/schemas/rag_state.py` — `RagState`(Query 그래프 상태), `IngestionState`(Ingestion 그래프 상태)
  - `app/schemas/response.py` — `QueryResponse`, `Source`, `Verification` (api-spec.md)
  - `app/schemas/__init__.py` — 주요 모델 re-export
  - `app/config.py` — `Settings` (pydantic-settings): source.type, Qdrant/Mongo/MySQL/OpenAI, 모델명
  - `tests/schemas/*`, `tests/test_config.py`
- **수정하지 않을 파일**: `app/` 그 외, 다른 팀원 담당 영역
- **구현 단계** (테스트 우선): ① 테스트 케이스 작성 → ② `app/schemas` 구현 → ③ `app/config.py` 구현 → ④ `./scripts/verify.sh`
- **테스트 계획**:
  - enums 값이 설계 문서와 정합 (DocType=incident/operation/faq/meeting/adr/troubleshoot 등)
  - `PageObject` 필수 필드 검증, `is_acl_missing` 식별(둘 다 빈 배열 → True), `Attachment` 검증
  - `ChunkMetadata` 19종 필드, `make_chunk_id` 멱등성(동일 입력 → 동일 id, UUID 미사용)
  - `QueryResponse` round-trip(직렬화/역직렬화), 첨부 전용 필드 Optional 동작
  - `Settings` 환경 변수 없이 기본값 인스턴스화 + env override 동작
- **문서 수정 필요 여부**: 없음 (스키마는 `docs/db-schema.md`·`docs/api-spec.md`·`docs/rag-pipeline-design.md` §7과 정합 확인만)
- **위험 요소**: PageObject 계약 미동결 시 재작업 가능 — 변경 시 영향은 어댑터(feature2)·청커(feature3·4)에 국한
- **완료 기준**: 모든 스키마 모델이 설계 문서와 정합 / 단위 테스트 전체 통과 / `Settings()` 무인자 인스턴스화 가능 / `verify` 통과 / `working-log.md` 갱신

작업 항목:

- [x] enums / PageObject·Attachment / Chunk·ChunkMetadata·make_chunk_id / RagState·IngestionState / 응답 스키마 정의
- [x] `app/config.py` — pydantic-settings 환경 설정
- [x] feature1 단위 테스트 통과 (35 passed)

### feature2: Document Source Adapter  ⏳ 진행 중 (데이터 계층 완료, Atlassian 어댑터 보류)

- 요구사항: 데이터 공급원 추상화. JSON 픽스처 어댑터 + Atlassian 직접 호출 어댑터
- 수정 대상: `app/adapters/{base,json_fixture,atlassian}.py`
- 테스트: `samples/*.json`으로 `JsonFixtureSourceAdapter` 계약 검증, mock HTTP로 `AtlassianSourceAdapter`의 `fetch_pages`/`list_active_ids`/`watch_changes` 검증
- 위험: `access_token`/`cloudid` 전달 방식 미확정 (선행 의존성 참조)

작업 항목:

- [x] `DocumentSourceAdapter` 인터페이스 + `ActiveIds`/`ChangeEvent` (`app/adapters/base.py`)
- [x] `JsonFixtureSourceAdapter` — `samples/*.json` → PageObject 변환 (92p 로드 검증, PoC ACL 합성)
- [ ] `AtlassianSourceAdapter` — `atlassian-python-api`로 `DATA-01`(Full Crawl) / `DATA-02`(CQL Delta Sync) / `DATA-03`(Space 목록) 호출 (`docs/atlassian-api.md`). **`access_token`/`cloudid` 전달 경로 확정 후 착수**

## Milestone B — Ingestion 파이프라인

### feature3: Adaptive Chunker (본문 6유형)  ✅ 완료 (2026-05-15)

- **작업 목표**: `samples/`의 92개 PageObject 본문(`body_html`)을 doc_type별 논리 단위로
  분할하여 `Chunk` 목록을 산출. 데이터 → 청크 단계 검증.
- **브랜치**: `feat/#1/rag-pipeline-skeleton` (기반 작업 연장)
- **규모상 2개 마일스톤으로 분할**:

  **feature3-A: 청킹 기반 (foundation)**
  - `app/ingestion/chunker/tokenizer.py` — `count_tokens()` 토큰 카운터.
    PoC 임시 구현(공백+CJK 휴리스틱, 의존성 없음). 실제 임베딩 모델 SentencePiece는
    품질 튜닝 단계에서 교체 — `docs/chunking-strategy.md` §7 정합
  - `app/ingestion/chunker/storage_format.py` — Confluence Storage Format(HTML) 공통 전처리
    (BeautifulSoup/lxml): 매크로 정규화, 코드블록 ``` 펜스 보존, `<table>` → 마크다운,
    이미지 alt/caption만 보존, 스마트 따옴표 정규화. 파싱 실패 시 plain text fallback
  - `app/ingestion/chunker/base.py` — 2단계 하이브리드 분할 공통 로직:
    2차 재분할(800토큰 초과 → 100토큰 오버랩), 하한선 병합(200토큰 미만),
    원자성 유지 유형 제외 처리, `make_chunk_id` 연동
  - 테스트: 토큰 카운터, HTML 전처리(매크로/코드블록/표/이미지), 2차 분할·하한선·원자성

  **feature3-B: 본문 6유형 분할기**
  - `app/ingestion/chunker/body.py` — doc_type별 1차 논리 단위 파서
    (incident 4블록 / operation H2 / faq Q&A쌍 / meeting 안건 / adr 전체1청크 / troubleshoot 케이스)
  - `app/ingestion/chunker/metadata.py` — 청크 메타데이터 19종 부착 + 무결성 규칙
  - `app/ingestion/chunker/__init__.py` — `chunk_page(page, doc_type) -> list[Chunk]` 엔트리
  - 테스트: 유형별 1차 분할, 원자성(FAQ·ADR·회의록), `samples/` 실제 본문 청킹 통합 테스트
- **doc_type 입력**: feature3은 `doc_type`을 입력으로 받는다(문서 분석기 Agent는 feature6).
  테스트·데모에서는 doc_type을 명시 주입하거나 라벨/제목 휴리스틱으로 임시 부여
- **문서 수정**: 청킹 규칙이 설계서와 달라지면 `docs/chunking-strategy.md` 함께 수정
- **완료 기준**: 6유형 분할·2단계 분할·원자성·메타데이터 무결성 단위 테스트 통과 /
  `samples/` 본문이 청크로 분할되는 통합 테스트 통과 / `verify` 통과

작업 항목:

- [x] feature3-A: tokenizer + storage_format(HTML 전처리) + chunker base(2단계 분할/하한선) — 24 tests, samples 92개 본문 전처리 오류 0건
- [x] feature3-B: 본문 6유형 분할기 + 메타데이터 부착 + chunk_page — 18 tests, samples 92p → 289 청크 오류 0건

### feature4: Adaptive Chunker (첨부 3유형)

- **작업 목표**: `samples/attachments/`의 첨부 파일을 `attachment_type`별 청킹 전략으로
  분할하여 `Chunk` 목록을 산출. 첨부 → 청크 단계 검증 (chunking-strategy.md §5).
- **브랜치**: `feat/#1/rag-pipeline-skeleton` (기반 작업 연장)
- **픽스처 가용성 기준 2개 마일스톤으로 분할**:

  **feature4-A: docx / xlsx 첨부 분할기**  ✅ 완료 (2026-05-15)
  - `app/ingestion/chunker/attachment.py` — 첨부 청킹
    - `infer_attachment_type(attachment)` — mime/확장자 기반 PoC 추정기
      (실제 분류는 첨부 분석기 [Pipeline]=feature6 책임)
    - docx: python-docx로 본문 블록(문단·표)을 문서 순서로 순회 → Heading 1/2/3
      경계 1차 분할(없으면 단락 fallback), 표는 마크다운 변환, 첫 헤딩 이전 preamble은
      첫 섹션에 부착. 원자성 없음 → `apply_size_rules`(2차 재분할·하한선 병합) 적용.
      `extracted_format=raw_text`, section_header=Heading 텍스트
    - xlsx: openpyxl로 시트 단위 1차 분할 → 시트 내 N행 그룹(기본 50행, 직렬화 결과
      800토큰 초과 시 25→10행 축소). 각 행을 `[<시트명>] <컬럼>: <값> | ...` 자연어
      직렬화, 컬럼명 헤더 매 청크 반복 부착, 빈 셀 생략, 헤더 누락 시 `col_1,col_2,...`
      부여. `extracted_format=sheet_serialized`, section_header=`[시트명] 행 N~M`
    - `build_attachment_metadata` — 첨부 청크 메타데이터 19종(`source_type=attachment`,
      `attachment_*`/`extracted_format` 채움, `doc_type`=attachment_type 값,
      `chunk_id`=make_chunk_id(parent_page_id, chunk_index, attachment_id), ACL·
      space_key·labels·webui_link·last_modified는 부모 페이지 상속)
    - `chunk_attachment(attachment, page, attachment_type=None) -> list[Chunk]` 엔트리
  - 재사용(feature3 자산): `ChunkDraft`/`apply_size_rules`/`count_tokens`/`make_chunk_id`
  - **버그 수정(`app/ingestion/chunker/base.py`)**: `merge_undersized`가 하한선을 채운
    직전 청크를 '봉인'하지 않아 작은 청크가 무한 누적 → 문서 전체가 한 청크로 붕괴하던
    버그를 수정. docx 첨부(Heading 섹션 다수가 200토큰 미만)에서 발견. 재현 테스트 선작성
    후 수정 — 본문 청킹도 함께 개선됨(`working-log.md` 참조)
  - 테스트: `infer_attachment_type`, docx Heading 계층 분할·표 마크다운·preamble 부착·
    헤딩 없는 fallback, xlsx 시트 분할·행 직렬화 형식·컬럼명 동봉·빈 셀 생략·50행 그룹
    분할·헤더 누락 fallback, 첨부 메타데이터 19종·결정론 chunk_id·ACL 상속,
    `samples/attachments/` 4건 통합 청킹, `merge_undersized` 봉인 회귀 테스트

  **feature4-B: PDF / CSV 첨부 분할기**  ✅ 완료 (2026-05-22) — feature4 전체 완료
  - PDF: ✅ 완료. PyMuPDF(fitz)로 폰트 휴리스틱 섹션 분할(본문 대비 ≥1.15배 큰 폰트 또는
    볼드 짧은 행), section_header=`p.<N>: <제목>`. 헤딩 미검출 시 단일 draft→`apply_size_rules`
    800토큰 슬라이딩 윈도우. fitz 추출 0건 시 pdfplumber 평문 폴백(지연 import). 암호화 PDF는
    `ATTACH_ENCRYPTED` ValueError. tmp_path에서 fitz로 PDF 생성해 테스트 6건 추가.
  - CSV: ✅ 완료. 표준 라이브러리 `csv` + 인코딩 자동감지(utf-8-sig/cp949 fallback,
    pandas 미사용 — 무거운 의존성 회피), xlsx 직렬화 로직(`_resolve_header`/
    `_group_sheet_rows`) 재사용. 단일 시트(파일명 stem)로 처리. tmp_path 픽스처로
    테스트 10건 추가. `_looks_like_header`를 수치 문자열도 비헤더로 보도록 보강(xlsx 무회귀).
- **수정하지 않을 파일**: `app/schemas/*`(ChunkMetadata 19종은 첨부 5종 이미 포함),
  `app/ingestion/chunker/{body,metadata,storage_format,tokenizer}.py`(feature3 완료분 — 재사용만),
  `app/adapters/*`, 다른 팀원 담당 영역
  (`base.py`는 당초 재사용만 예정이었으나 `merge_undersized` 붕괴 버그 발견으로 수정 — 위 참조)
- **문서 수정**: DB 스키마·청킹 규칙 변경 없음(db-schema.md §1.2·chunking-strategy.md §5 정합).
  구현 해석(docx 섹션 비원자성, xlsx 자체 oversize 처리)은 `working-log.md`에 기록
- **완료 기준**: docx Heading 분할·xlsx 행 직렬화·헤더 fallback·메타데이터 무결성 단위
  테스트 통과 / `samples/attachments/` 4건 통합 청킹 오류 0건 / `verify` 통과

작업 항목:

- [x] feature4-A: docx / xlsx 첨부 분할기 + 첨부 메타데이터 + chunk_attachment
- [x] feature4-B: PDF / CSV 첨부 분할기
  - [x] CSV 분할기 (2026-05-22, 표준 라이브러리 csv + 인코딩 fallback, xlsx 자산 재사용, 테스트 10건)
  - [x] PDF 분할기 (2026-05-22, fitz 폰트 휴리스틱 + pdfplumber 폴백 + 암호화 거부, 테스트 6건)

### feature5: Dual Embedding + Multi-Pool Vector Store [Pipeline + Storage]

- **작업 목표**: 청크를 Pool별 임베딩 입력으로 변환하고, Qdrant Multi-Pool에 적재할 Point
  payload를 구성하며, embedding_cache 기반 멱등성을 확보한다 (rag-pipeline-design.md §5,
  db-schema.md §1·§2.4). 청커 산출물(`Chunk`)을 실제 검색 가능한 색인으로 잇는 "다리".
- **브랜치**: `feat/#1/rag-pipeline-skeleton` (기반 작업 연장)
- **외부 의존성(e5-large 모델·Qdrant·MongoDB) 분리 위해 2개 마일스톤으로 분할**:

  **feature5-A: 임베딩 입력·payload·멱등성 순수 로직**  ✅ 완료 (2026-05-15)
  - `app/ingestion/vector_store.py` [Storage] — Pool 이름 상수(`TITLE_POOL`/`CONTENT_POOL`/
    `LABEL_POOL`, config.py 기본값과 정합) + `build_point_payload(chunk, version_number)`:
    `Chunk` → Qdrant Point payload dict(db-schema.md §1.2의 19필드). `version_number`는
    ChunkMetadata에 없으므로(페이지 단위 값) 부모 PageObject에서 별도 인자로 주입.
    Point id는 chunk_id(feature1 `make_chunk_id`)
  - `app/ingestion/embedding.py` [Pipeline] — `pool_embedding_texts(chunk)`: Pool별 임베딩
    입력 텍스트 구성(title=page_title+section_header / 첨부는 attachment_filename+
    section_header, content=청크 본문, label=labels+space_key+doc_type) +
    `should_skip_embedding(version_number, cached_version)`: 멱등성 판정(app/CLAUDE.md §4)
  - 외부 의존성 0 — e5-large·Qdrant·MongoDB 없이 완전히 단위테스트 가능
  - 테스트: payload 19필드 매핑·page/attachment 분기·text_preview 200자·version_number
    주입, pool별 텍스트 구성, 멱등성 판정(동일 버전 skip / 캐시 없음 / 버전 불일치)

  **feature5-B: 실제 임베딩·Qdrant·MongoDB 클라이언트 연동**  ✅ 완료 (2026-05-18)
  - Dense(`intfloat/multilingual-e5-large`, 1024d)·Sparse(BM25) 실제 임베딩, Qdrant 3 Pool
    Collection 생성·Named Vector upsert, MongoDB `embedding_cache` I/O. e5의 `passage:`
    프리픽스 등 모델별 처리도 여기서.
  - [의존성 방향 — 결정·구현 완료] 무거운 의존성 방향을 다음으로 확정해 구현했다:
    Dense=`sentence-transformers`(e5-large), Sparse=`fastembed`(`Qdrant/bm25`),
    Vector Store=`qdrant-client`(`:memory:` PoC / 서버 운영 겸용), Cache·Lookup=`pymongo`.
    실 모델 import는 `build_real_deps` 본문 lazy 처리라 embedding extra 미설치 환경에서도
    PoC 경로·모듈 import는 무영향. 임베딩·Qdrant·Mongo는 어댑터/클라이언트 계층으로
    분리(app/CLAUDE.md §8).
  - 구현체: `app/ingestion/embedder/{base,dense,sparse}.py`(E5/BM25 어댑터+ABC+Fake, `633d821`),
    `app/storage/qdrant_client.py`(`QdrantPoolStore`, `2835ccd`),
    `app/storage/mongo_cache.py`(`MongoEmbeddingCache`)·`chunk_lookup.py`(`MongoChunkTextLookup`).
    `build_real_deps`/`build_real_ingestion_deps`(`app/api/deps.py`)가 실 어댑터를 부트스트랩.
    feature17c-4~12 재적재·재평가(`844cd69`, Precision@3 68→80%)로 끝-끝 실 경로 검증됨.
- **수정하지 않을 파일**: `app/schemas/*`(ChunkMetadata에 version_number 부재 — payload
  빌더가 별도 인자로 받아 해소, 스키마 변경 안 함), `app/llm/*`, 다른 팀원 담당 영역
- **문서 수정**: feature5-A는 db-schema.md §1.2 payload 스키마를 구현만 — 변경 없음(정합 확인).
  Pool/스키마를 바꾸게 되면 `docs/db-schema.md` 함께 수정
- **완료 기준(5-A)**: 단위 테스트 전체 통과 / `verify` 통과 / `working-log.md` 갱신

작업 항목:

- [x] feature5-A: 임베딩 입력·payload·멱등성 순수 로직
- [x] feature5-B: 실제 임베딩·Qdrant·MongoDB 클라이언트 연동 (2026-05-18, `633d821`/`2835ccd` +
  `build_real_deps` 배선 + feature17c 실 경로 재평가 검증. 의존성 방향: sentence-transformers /
  fastembed / qdrant-client / pymongo)

### feature6: 문서 분석기 + 첨부 분석기 + Ingestion 그래프 — ⚠ 담당 분리

- **본 담당자 몫(Pipeline + Storage)**: 첨부 파일 분석기(`app/ingestion/attachment_analyzer.py`),
  삭제 동기화(`app/ingestion/sync.py`), `ingestion_jobs` 기록 헬퍼(`app/storage/jobs.py` —
  외부 저장소 어댑터는 `app/storage/` 패키지 일관성 정합, `app/CLAUDE.md` §8).
- **Agent 담당자 몫**: 문서 분석기(`app/ingestion/document_analyzer.py` [Agent]).
- **통합 지점**: Ingestion 그래프 조립(`app/pipeline/ingestion_graph.py`) — Agent 노드 stub →
  전달 후 교체.
- 테스트: 첨부 분석·Reconciliation 고스트 삭제·그래프 흐름(본 담당자, mock/stub),
  mock LLM으로 doc_type 판별·캐싱·Fallback(Agent 담당자)

작업 항목:

- [x] (본 담당자) 첨부 분석기 [Pipeline] — Phase 1 완료 (2026-05-18, `4c6c2dc`)
- [x] (본 담당자) `ingestion_jobs` 기록 헬퍼 [Storage] — Phase 2 완료 (2026-05-18, `152d2e9`)
- [x] (본 담당자) 삭제 동기화 [Pipeline] — Phase 3 완료 (2026-05-18, `8ceec58`)
- [x] (본 담당자) Ingestion 그래프 조립 — Phase 4 완료 (2026-05-18) — feature6 종결
- [ ] (Agent 담당자) 문서 분석기 [Agent]

## Milestone C — Query 파이프라인

> **진행 메모 (2026-05-15 갱신)**: RAG 담당자의 기획서 범위는 Query 파이프라인이며,
> **Agent 컴포넌트는 별도 담당자 몫**이다 — Agent 코드·파일은 추후 전달받아 병합한다.
> 따라서 본 담당자는 각 feature의 **[Pipeline]/[Storage] 부분만** 진행하고 **[Agent] 부분은
> 건너뛴다.** Ingestion(Milestone B)은 feature4-A까지 완료, 이후 Query(Milestone C)로 전환.
>
> **Agent / Pipeline 경계와 병합 방식:**
> - Agent 담당자 전달분: 질의 라우터·멀티턴 히스토리(feature8 전체), 답변 생성기·검증 2단계
>   LLM 평가자(feature10 일부), 문서 분석기(feature6 일부), 그리고 `app/llm/`(Agent 인프라).
> - Agent 노드와 Pipeline 노드는 **서로 직접 호출하지 않는다.** 공유 seam은 (1) `RagState`
>   — feature1에서 동결된 상태 계약, 각 노드가 필드를 읽고 쓴다, (2) LangGraph 그래프(feature11)
>   — 노드를 순서대로 배선, (3) 합의된 모듈 경로·노드 시그니처(각 feature에 명시).
> - 본 담당자의 Pipeline 노드는 RagState 필드 계약만 지키면 Agent 코드와 독립적으로 구현·
>   단위테스트된다. 그래프 조립 시 Agent 노드는 stub/fake로 대체했다가 실제 코드 전달 시
>   교체한다 (app/CLAUDE.md §6).
>
> **진행 순서**: feature7(완료) → feature9-A → feature10[Pipeline] → feature11[Pipeline:
> 포맷터] → feature5(다리) → feature9-B → feature11(그래프·API 조립) → feature6[Pipeline]
> → feature4-B. feature1·2(공통 기반)는 양 파이프라인 공용이라 그대로 활용한다.

### feature7: ACL Pre-filtering + @enforce_acl  ✅ 완료 (2026-05-15)

- **작업 목표**: 사용자 단위 검색의 권한 경계를 시스템 단에서 강제. JWT에서 사용자 식별을
  추출하고, Qdrant 검색에 항상 주입되는 ACL 필터를 생성하며, ACL 없는 검색 호출을
  데코레이터로 거부한다 (rag-pipeline-design.md §6 4.2, app/CLAUDE.md §3, db-schema.md §1.4).
- **브랜치**: `feat/#1/rag-pipeline-skeleton` (기반 작업 연장)
- **수정 대상 파일**:
  - `app/query/acl.py` (신규)
    - `extract_principal(jwt) -> Principal` — JWT payload를 stdlib base64+json으로 디코드해
      `sub`(user_id)·`groups`만 추출. **서명은 검증하지 않는다** — 인증/JWT 발급은 BFF 책임
      (api-spec.md). 형식 오류·`sub` 누락 시 `PrincipalExtractionError`(API의 `UNAUTHORIZED` 대응)
    - `build_acl_filter(user_id, groups) -> dict` — `allowed_groups`가 사용자 그룹 중 하나와
      매칭 **OR** `allowed_users`가 user_id 포함, 하는 Qdrant `should` 필터 dict 생성
      (`RagState.acl_filter`가 `dict[str, Any]` 계약). ACL 필드 모델은 `allowed_groups`/
      `allowed_users` 채택 결정됨 — 이 함수만 교체하면 다른 모델로 전환 가능 (app/CLAUDE.md §3)
    - `ACLViolationError` + `@enforce_acl` — 검색 함수에 유효한 `acl_filter` 인자가 없으면
      거부. 데코레이션 시점에 `acl_filter` 파라미터 존재를 강제하고, 호출 시점에 필터
      누락·무효를 `ACLViolationError`로 거부. ACL 검사는 호출 전이라 sync/async 함수 모두 지원
  - `app/query/__init__.py` — re-export 갱신 (adapters/·chunker/와 동일 패턴)
  - `tests/query/__init__.py`, `tests/query/test_acl.py` (신규)
- **수정하지 않을 파일**: `app/schemas/*`(RagState가 이미 `user_id`/`groups`/`acl_filter` 보유 —
  변경 불필요), `app/` 그 외, 다른 팀원 담당 영역
- **구현 단계** (테스트 우선): ① 테스트 작성 → ② `acl.py` 구현 → ③ `__init__.py` re-export →
  ④ `./scripts/verify.sh`
- **테스트 계획**:
  - `extract_principal`: 정상 JWT → Principal, groups 누락 시 `[]` 기본값, 형식 오류·payload
    디코드 실패·`sub` 누락 시 `PrincipalExtractionError`
  - `build_acl_filter`: `should` OR 구조(allowed_groups any / allowed_users any), 빈 groups 처리
  - `@enforce_acl`: 유효 필터 시 정상 호출, 필터 누락/None/무효 시 `ACLViolationError`,
    `acl_filter` 파라미터 없는 함수 데코레이션 시 `TypeError`
- **문서 수정 필요 여부**: 없음 (acl.py는 db-schema.md §1.4·api-spec.md와 정합 확인만)
- **위험 요소**: 보안 핵심 — ACL 우회 불가 구조 검증 필수. 필터 생성 로직은 단일 함수로
  격리해 ACL 모델 변경 시 교체 지점을 한정
- **완료 기준**: 단위 테스트 전체 통과 / `@enforce_acl` 우회 시도가 `ACLViolationError`로
  거부됨을 테스트로 확인 / `verify` 통과 / `working-log.md` 갱신

작업 항목:

- [x] ACL 필터 생성 + `@enforce_acl` 데코레이터 + JWT 클레임 추출

### feature8: 질의 라우터 + 멀티턴 히스토리 [Agent] — ⚙ 통합 진행 중

- 질의 라우터·멀티턴 히스토리 관리자는 **둘 다 Agent 컴포넌트**이며 Agent 담당자가 구현해
  전달한다. 본 담당자(Pipeline/통합)는 전달받은 Agent 코드를 vendoring하고 RagState 어댑터
  노드로 통합한다.
- **병합 계약**: 노드 시그니처 `(state: RagState) -> RagState`. 라우터는 RagState의
  `intent`/`rewritten_queries`/`metadata_filters`/`pool_weights`/`target_llm`를 채운다.

  **feature8-멀티턴 히스토리: history-manager-agent 통합**  ⚙ vendoring 완료, 어댑터 진행 예정
  - **전달분**: `ai-agent` 저장소의 `history-manager-agent` — 자체 pyproject·`src/` 레이아웃·
    스키마(dataclass)·테스트를 가진 독립 패키지. 작성자 Codex.
  - **vendoring (완료, 2026-05-15)**: `src/history_manager_agent/**` → 저장소 루트
    `history_manager_agent/`(무수정), `tests/**` → `tests/history_manager_agent/**`(무수정 +
    pytest 패키지 마커 `__init__.py`만 추가), `history-manager-agent.md` → `docs/`.
    `pyproject.toml` — `packages.find`에 `history_manager_agent*` 추가, `[tool.ruff]
    extend-exclude`로 벤더 코드를 RAG lint/format 대상에서 제외(원본 무수정 보존). 벤더
    테스트 76개는 RAG `pytest`로 함께 실행되어 통과
  - **어댑터 노드 (완료, 2026-05-15)**: `app/query/history.py` — `manage_history(state:
    RagState, *, provider=None) -> RagState`. 파일 기반 워크플로 대신 agent의 조립 가능한
    로직 함수(`normalize_history_input_payload`/`classify_history`/`apply_context_policy`/
    `build_question_result`)를 in-process로 호출. 기본 provider는 `FakeHistoryLLMProvider`
    (PoC·테스트), 실제 `OpenAIHistoryLLMProvider` 주입 가능
  - **RagState 확장 (완료)**: agent 출력(`history_decision`/`contextualized_question`/
    `preserved_context`/`reset_required`/`confidence`/`reason`/`warnings`)은 RagState의
    `history`/`needs_search`에 1:1로 안 맞음. → `app/schemas/rag_state.py`에 `HistoryDecision`
    Pydantic 모델 추가하고 `RagState.history_decision: HistoryDecision | None` 필드 신설.
    매핑: `RagState.query`는 원문 유지(비파괴), `contextualized_question`은
    `history_decision`에 담아 다운스트림이 선택 사용. `needs_search`는 agent MVP가 검색스킵
    신호를 내지 않으므로 기본 `True` 유지. `conversation_id` 없으면 어댑터가 new_topic으로
    단축 처리
  - **테스트**: `tests/query/test_history.py` — RagState→agent 입력 변환(HistoryTurn→
    ConversationTurn, turn_id/created_at 합성), 분류 결과별 RagState 매핑, conversation_id
    없는 경우 단축, FakeHistoryLLMProvider 주입

작업 항목:

- [x] history-manager-agent vendoring (패키지·테스트·스펙 문서, pyproject 갱신)
- [x] `app/query/history.py` 어댑터 노드 + `RagState.history_decision` 확장 + 테스트
- [ ] (Agent 담당자) 질의 라우터 — 전달 후 동일 방식으로 통합

### feature9: Multi-Pool Hybrid Search + Cross-Encoder 재순위화 [Pipeline]

- **작업 목표**: 3개 Pool 검색 결과를 RRF로 융합·가중 합산해 Top-20을 뽑고, Cross-Encoder
  재순위화로 Top-5를 선정한다 (rag-pipeline-design.md §6 4.5, §8). 전부 [Pipeline] — 본 담당자 몫.
- **브랜치**: `feat/#1/rag-pipeline-skeleton` (기반 작업 연장)
- **외부 의존성(임베딩 모델·Qdrant·Cross-Encoder 모델) 분리 위해 2개 마일스톤으로 분할**:

  **feature9-A: 검색·재순위화 핵심 로직 (순수 함수)**  ✅ 완료 (2026-05-15)
  - `app/query/search.py` — 순수 함수: `reciprocal_rank_fusion`(RRF k=60, Pool 내부
    dense+sparse 융합), `merge_pools`(Pool 가중 합산), `select_top_candidates`(Top-20 선정,
    동점 결정론 정렬), `fuse_and_rank`(세 단계 결합 엔트리)
  - `app/query/rerank.py` — 순수 함수: `select_reranked`(Cross-Encoder 점수 → Top-5,
    5위 < 0.30이면 Top-3 축소, 최고 < 0.20이면 저신뢰 플래그) + `RerankResult` 데이터클래스
  - `app/query/__init__.py` — re-export 갱신
  - 외부 의존성 0 — 임베딩·Qdrant·Cross-Encoder 모델 없이 완전히 단위테스트 가능. feature9의
    회귀 보호 핵심 로직. RagState 통합·I/O 배선은 9-B 책임
  - 테스트: RRF 점수·순위, Pool 가중 합산, Top-N 선정·동점 정렬, Top-5/Top-3 축소,
    저신뢰 임계, 빈 입력(0건) 처리

  **feature9-B: 검색·재순위화 노드 오케스트레이션**  ✅ 완료 (2026-05-18)
  - 쿼리 임베딩 + Qdrant 3-pool 검색 + Cross-Encoder 추론을 9-A 로직에 연결하는 LangGraph
    노드(`hybrid_search`/`cross_encoder_rerank`, `(state: RagState) -> RagState`).
    `candidates`(Top-20)·`top_chunks`(Top-5)를 RagState에 채운다
  - [선행 의존성 — 해소] feature5-B(E5/BM25/Qdrant) 및 Cross-Encoder 어댑터를 함께 확보해
    착수. 임베딩·Qdrant·Cross-Encoder는 어댑터/클라이언트 계층으로 분리(app/CLAUDE.md §8).
  - 구현체: `app/query/reranker/{base,cross_encoder}.py`(`CrossEncoderReranker` ABC+Fake+
    `CrossEncoderRerankerImpl`, `4f2b0f3`), `app/query/search_node.py`(`hybrid_search`,
    `6e6753e`), `app/query/rerank_node.py`(`cross_encoder_rerank` Top-5+sources, `b080bdd`).
    `query_graph.py`가 두 노드를 직접 배선(stub 아님). feature17c-4~12 실 경로 재평가로 검증됨.
- **수정하지 않을 파일**: `app/schemas/*`(RagState가 이미 candidates·top_chunks 보유),
  `app/llm/*`(Agent 인프라), `app/ingestion/*`, 다른 팀원 담당 영역
- **완료 기준(9-A)**: 순수 함수 단위 테스트 전체 통과 / `verify` 통과 / `working-log.md` 갱신

작업 항목:

- [x] feature9-A: 검색·재순위화 핵심 로직 (RRF / Pool 가중 합산 / Top-K 선정 / 저신뢰 분기)
- [x] feature9-B: 검색·재순위화 노드 오케스트레이션 (2026-05-18, `4f2b0f3`/`6e6753e`/`b080bdd` —
  CrossEncoderRerankerImpl + hybrid_search/cross_encoder_rerank 노드 + query_graph 배선)

### feature10: 답변 생성기 + 답변 검증 — ⚠ 담당 분리

- **본 담당자 몫(Pipeline)**: 답변 검증 **1단계 규칙 매칭** — `app/query/verifier.py`.
  - **작업 목표**: 생성된 답변을 문장 단위로 분해해, 각 문장의 검증 토큰(수치·구조적 식별자)이
    인용한 청크 텍스트에 나타나는지 규칙으로 대조한다. 확인되지 않은 토큰이 있는 문장은
    의심(suspicious)으로 FLAG해 2단계 LLM 평가자로 넘기고, 그 외는 PASS로 확정한다
    (rag-pipeline-design.md §6 4.7, conventions.md §5.5).
  - **수정 대상**: `app/query/verifier.py`(신규, 1단계 부분), `app/query/__init__.py`,
    `tests/query/test_verifier.py`(신규)
  - **구현**: `verify_answer_rules(answer, top_chunks) -> RuleVerificationResult`.
    헬퍼 — 문장 분리(PoC 휴리스틱), `[#n]` 인용 추출, 인용 청크 텍스트 수집, 검증 토큰
    추출(수치·구조적 식별자 — Mecab 미사용 PoC 휴리스틱), 토큰 근거 대조.
    `SentenceCheck`(문장별 결과) + `RuleVerificationResult`(`suspicious_sentences`/
    `has_suspicious_sentences`/`passed_verifications` 접근자).
  - **병합 계약**: `RuleVerificationResult.passed_verifications()`는 PASS 문장의 최종
    `Verification`(status=PASS)을 준다. `suspicious_sentences`는 2단계 평가자(Agent)가
    받아 `SUPPORTED`/`NOT_SUPPORTED`를 판정하고, 두 결과를 병합해 RagState.verification을
    만든다(병합·`NOT_SUPPORTED` 비율 차단은 feature11 통합 지점).
  - **수정하지 않을 파일**: `app/schemas/*`(Verification 스키마 기존 활용), `app/llm/*`,
    `app/query/generator.py`(Agent), 다른 팀원 담당 영역
  - **테스트**: 문장 분리, 인용 추출, 검증 토큰 근거 대조, 근거 있는 문장 PASS / 미검증
    토큰·미인용 claim 문장 suspicious, 필러 문장(검증 토큰 없음) PASS, 빈 답변,
    `passed_verifications`/`suspicious_sentences` 접근자
  - **완료 기준**: 단위 테스트 전체 통과 / `verify` 통과 / `working-log.md` 갱신
- **Agent 담당자 몫**: 답변 생성기(`app/query/generator.py` [Agent]), 답변 검증 **2단계
  LLM 평가자**(`SUPPORTED`/`NOT_SUPPORTED`, `app/query/verifier.py`에 2단계 섹션 추가).

작업 항목:

- [x] (본 담당자) 답변 검증 1단계 규칙 매칭 [Pipeline]
- [ ] (Agent 담당자) 답변 생성기 [Agent] + 검증 2단계 LLM 평가자 [Agent]

### feature11: 응답 포맷터 + Query 그래프 + API — ⚠ 일부 통합 지점

- **본 담당자 몫(Pipeline)**: 응답 포맷터 — `app/query/formatter.py`.
  - **작업 목표**: 생성·검증을 거친 답변을 `QueryResponse`(UI JSON)로 변환하고,
    api-spec.md "표준 분기 응답" 규칙을 적용한다 — Cross-Encoder 최고 점수가 낮으면
    저신뢰 분기(`feedback_enabled=false`), NOT_SUPPORTED 비율 > 50%면 답변 차단·대체
    (rag-pipeline-design.md §6 4.8, api-spec.md).
  - **수정 대상**: `app/query/formatter.py`(신규), `app/query/__init__.py`,
    `tests/query/test_formatter.py`(신규)
  - **구현**: `format_response(answer, sources, verification, intent, used_llm,
    latency_ms) -> QueryResponse` 순수 함수 + 헬퍼(`_is_low_confidence`·
    `_not_supported_ratio`) + 상수(`LOW_CONFIDENCE_SCORE`=20·`VERIFICATION_BLOCK_RATIO`=
    0.5·`BLOCKED_ANSWER_MESSAGE`). feature9-A처럼 순수 로직 우선 — RagState→인자 추출
    노드 래퍼는 그래프 조립 단계에서.
  - **scoping**: `Source` 객체 생성(Chunk+Cross-Encoder 점수 → Source)은 feature9-B
    책임(점수를 가진 단계). 포맷터는 완성된 `Source`를 입력으로 받는다(`RagState.sources`가
    이미 `list[Source]`). 검색 0건 early-exit는 그래프(아래 통합 지점) 몫 — 포맷터는
    "생성된 답변을 응답으로 변환"만 한다.
  - **수정하지 않을 파일**: `app/schemas/*`(QueryResponse·Source 기존 활용), `app/llm/*`,
    `app/pipeline/*`·`app/api/*`(통합 지점 — 아래), 다른 팀원 담당 영역
  - **테스트**: 정상 응답(feedback_enabled=True), 저신뢰 분기(최고 Source 점수 < 20),
    검증 차단(NOT_SUPPORTED 비율 > 50% → 답변 대체), 경계값, 차단 우선순위,
    sources/verification 통과
  - **완료 기준**: 단위 테스트 전체 통과 / `verify` 통과 / `working-log.md` 갱신
- **통합 지점**: Query 그래프 조립(`app/pipeline/query_graph.py`)·FastAPI 라우트(`app/api/*`)는
  Agent 노드 + Pipeline 노드를 배선한다. Agent 노드는 stub/fake로 두고 구현·end-to-end
  테스트한 뒤, Agent 코드 전달 시 교체. 그래프 조립은 feature5·9-B 이후가 적절.
- 문서 수정: API 변경 시 `docs/api-spec.md`

작업 항목:

- [x] (본 담당자) 응답 포맷터 [Pipeline]
- [x] Query LangGraph 조립 + Agent stub 3종 — Phase 1 완료 (2026-05-18,
  `app/pipeline/{stubs,nodes,query_graph}.py`. Agent 코드 전달 시 `QueryGraphDeps`
  3개 필드만 교체)
- [x] FastAPI 라우트(SSE) — Phase 2 완료 (2026-05-18,
  `app/api/{main,routes,errors,deps}.py`. PoC: `:memory:` Qdrant + Fake +
  samples 자동 인덱싱. token 1회 송신, Agent 통합 시 다중 송신으로 확장)

---

## 진행 규칙 (요약)

1. feature 단위로만 작업한다. 다음 feature는 새 세션 또는 `/clear` 후 시작한다.
2. 테스트 케이스 정리 → 실패 테스트 작성 → 최소 구현 → 테스트 통과 순서를 지킨다.
3. 완료 후 `./scripts/verify.sh`(format → lint → test)를 실행한다.
4. `git diff`로 변경 범위를 확인하고 `docs/ai/working-log.md`를 업데이트한 뒤 커밋한다.
5. Agent 컴포넌트는 LLM 응답을 mock/fake로 대체해 테스트한다. 외부 의존성(Qdrant/Mongo/MySQL)도 동일.

---

## Milestone D — 운영성 마무리 (2026-05-19 이후, ML 코드 리뷰 반영)

> **배경**: Agent 통합 3/4 완료 + Mode B 실 GPT-4o 시연 성공 (`ac66fee`) 후 잔여 작업.
> 2026-05-18 ML 코드 리뷰(`0518_RAG.pdf`) 4개 항목 + 본 담당자 영역 자체 발견한
> 운영성 fix 3건을 묶어 진행한다. 본 Milestone 완료 시 본 담당자 영역 진척도 100%
> + 운영 진입 직전 단계 완성.

### feature12: ML 코드 리뷰 (PDF 1+4) + 운영성 fix — P0 ✅ 다음 세션 즉시

본 담당자가 단독으로 진행 가능한 묶음. 외부 협의 불필요.

- **(PDF #1) .gitignore 보완** — `.env` push 방지 + 표준 ignore 패턴
  - 기존 `.gitignore` 와 머지 (중복 제거)
  - PDF 명시 패턴: Python / Virtual environments / Tooling caches / Secrets·env
    (`.env`, `.env.*`, `!.env.example`) / IDE·OS / Logs
- **(PDF #4) Prometheus 모니터링 의존성 추가**
  - `pyproject.toml` 에 `prometheus-fastapi-instrumentator` (또는 동등) 추가
  - `app/api/main.py` `create_app` 에 instrumentator wiring + `/metrics` 엔드포인트
  - 결정점 (사용자 확인): 라이브러리 선택 / 메트릭 범위 (HTTP만 vs LLM 커스텀) /
    `/metrics` 노출 위치 (root vs `/api/v1/metrics`) / 인증 적용 여부 (BFF 책임이라
    본 저장소는 비인증 유지)
- **라우터 어댑터 LangGraph config 충돌 fix** — `app/query/router.py` 도 generator
  와 동일한 LangGraph RunnableConfig dict override 영향 받음 (Mode B 시연에서 fallback
  떨어지지만 OPERATION_GUIDE 라 잘 안 보임). `config` → `routing_config` keyword-only
  분리 + `query_graph.py` partial wiring 갱신 + `tests/query/test_router.py` 회귀
- **`app/config.py` 기본값 fix**
  - `cross_encoder_model` 기본값에 `-v2` 추가 (`cross-encoder/ms-marco-MiniLM-L-12-v2`)
    — 현재 `.env` 로 우회 중이지만 코드 기본값도 fix
  - `build_real_deps` 에서 routing/verifier provider 에 `settings.openai_api_key
    .get_secret_value()` 명시 전달 → vendoring agent 의 `os.getenv("OPENAI_API_KEY")`
    조회 없이 동작 → `.env` 의 `OPENAI_API_KEY` 중복 환경변수 불필요
- **테스트**:
  - `tests/api/test_query_route.py` `/metrics` 엔드포인트 회귀
  - `tests/query/test_router.py` `routing_config` rename 영향
  - `tests/api/test_deps.py` api_key 명시 전달 검증
- **완료 기준**: ruff/mypy/pytest 통과 / Mode B 운영에서 라우터·`/metrics` 동작 확인 /
  working-log + commit + push

작업 항목:

- [x] (PDF #1) .gitignore 보완 — `78286ea`
- [x] (PDF #4) Prometheus instrumentator + /metrics — `78286ea`
- [x] 라우터 LangGraph config rename — `78286ea`
- [x] config.py 기본값 + build_real_deps api_key 명시 전달 — `78286ea`

### feature13: ML 코드 리뷰 (PDF 2+3) — P1 ⚙ PDF #2 코드 마이그레이션 완료 / PDF #3 BE 대기

BE 통합 API 스펙 수신(`api-spec-BE-adjust.md`, 2026-05-21). PDF #2(API Spec + 코드
마이그레이션)는 완료, PDF #3(ACL 컬럼 정합)은 BE 확정 대기.

- **(PDF #2) API Spec — BE 변경사항 업데이트**
  - [x] BE 변경분 수신 (`api-spec-BE-adjust.md`) — §2-1 `/ml/query` + §1-1 SSE 이벤트 형식
  - [x] `docs/api-spec.md` 갱신 (2026-05-22) — 목표 계약 반영:
    - 엔드포인트 `/api/v1/rag/query` → `/ml/query`
    - 요청 본문 재정의: `jwt` 제거 → `question`/`conversationId`/`history[]`/`userId`/
      `groups[]`/`spaceKey`/`accessToken`/`cloudId`
    - SSE 이벤트: `token`=`{"content"}`, `sources`=`{"sources":[...]}`(필드 `pageId`/
      `spaceId`/`spaceName`/`url`/`updatedAt`(KST)/`relevanceScore`(0~1)), `verification`=
      집계 `{"confidenceScore","verificationResult"}`(+`PARTIALLY_SUPPORTED`), `done`=`{}`
      (messageId는 BFF 주입), `error` 이벤트 신규
    - `meta` 이벤트 제거(intent/used_llm/feedback_enabled/latency_ms → 내부 메트릭만)
  - [x] **코드 마이그레이션 완료 (2026-05-26, /ml/query 완전 전환 + spaceKey passthrough)**:
    - `app/api/routes.py` — 엔드포인트 `/ml/query` 전환, `QueryRequest` 재정의(question/
      userId/groups/spaceKey/conversationId/history/accessToken?/cloudId?/stream, camelCase
      alias), JWT 추출 제거(userId/groups 직접 수신), SSE 형식 변경(token=`{content}`/sources
      래핑/verification 집계/done=`{}`), `meta` 송신 제거, 오류 → SSE `error` 이벤트
    - `app/schemas/response.py` — `Source`에 page_id/space_id/space_name + `to_bff_payload`
      (relevanceScore 0~1 / updatedAt KST / 필드 rename). `VerificationSummary.from_sentences`
      집계 모델 신설(confidenceScore + verificationResult). `feedback_enabled`는 내부 유지
    - `app/schemas/enums.py` — `VerificationResult`(SUPPORTED/PARTIALLY_SUPPORTED/NOT_SUPPORTED)
      집계 전용 enum 신설(문장별 `VerificationStatus`는 무변경)
    - `app/schemas/rag_state.py` — `space_key` 필드 추가(passthrough)
    - `app/api/errors.py` — `ML_SERVER_ERROR` 코드 추가
    - `tests/api/test_query_route.py` 신규 계약 회귀 + `tests/schemas/test_response.py` 집계/
      직렬화 단위 테스트 추가
    - 범위 밖: `accessToken`/`cloudId`(3단계 수신만), `extract_principal`은 acl.py에 보존
      (라우트만 사용 중단), spaceKey 검색 필터 반영은 후속
    - ⚠ (2026-06-04 v2.4 정합으로 **superseded**) — `spaceKey`·`RagState.space_key`는 이후 제거됨
      (cross-space 전환). 본 단계 기록은 이력이며 최신 계약은 위 line 31·`docs/api-spec.md` v2.5 참조.
- **(PDF #3) Schema — user ACL + Confluence call 명세 정합**
  - 대기 (BE 확정 필요):
    - user ACL (권한) 관련 column 이 BE 에서 어떻게 전달되는지
    - Confluence 데이터 call 명세
  - 갱신 대상:
    - `docs/db-schema.md`
    - `app/schemas/chunk.py` `ChunkMetadata` (allowed_groups / allowed_users 등)
    - `app/query/acl.py` `build_acl_filter` 정합
    - `app/adapters/json_fixture.py` `_synthesize_acl` (현재 PoC 합성 → 실 명세)
    - tests 회귀
- **완료 기준**: 명세 합의 → 스키마 변경 → 회귀 테스트 통과 → docs 동반 갱신 →
  working-log + commit + push

작업 항목:

- [x] (PDF #2) API Spec 갱신 (2026-05-22) + 코드 마이그레이션 (2026-05-26, /ml/query 전환)
- [ ] (PDF #3) ACL 컬럼 스키마 정합 (BE 협의 후)

### feature14: (A) SSE token streaming 라우트 통합 — P2

- **목표**: 설계서 §4.6.4 KPI "P95 5초", "토큰당 25~40ms" 달성
- **수정 대상**: `app/api/routes.py` `query_route` 에 `stream` query parameter 분기
- **흐름**: graph 흐름을 search/rerank 까지만 실행 → top_chunks 확보 후
  `stream_openai_answer` (openai_streaming.py, 본 commit `5f9311b` 에서 작성 완료)
  로 답변 생성 대체 → token chunk 다중 송신 → 답변 완료 후 sources/verification/
  meta/done 송신
- **수정하지 않을 파일**: `app/pipeline/query_graph.py` (그래프 자체는 그대로),
  `answer_generation_agent/**` (vendoring 무수정)
- **테스트**: `tests/api/test_query_route.py` 에 stream=true 분기 회귀
- **완료 기준**: 테스트 통과 / Mode B 운영에서 첫 토큰 1초 내 도달 확인

작업 항목:

- [x] SSE 라우트 streaming 분기 + 회귀 — `build_query_graph_for_streaming`
  helper + `QueryRequest.stream` + `_streaming_event_stream` + PoC fallback +
  partial graph 회귀 2건 + 라우트 회귀 2건 (다음 commit)

### feature15: (C) Rate Limit fallback — P2

- **목표**: 설계서 §4.6.5 — Rate Limit (429) → GPT-4o-mini 자동 다운그레이드 + 재시도
- **수정 대상**: `app/query/generator.py` `manage_generator` 가 `AnswerProviderError(
  error_type='rate_limit_error')` 캐치 후 `selected_config.fallback_model` 로 재시도 +
  `RagState.verification` 에 다운그레이드 note 추가
- **테스트**: 429 mock 시 fallback_model 호출 + verification.note 기록 회귀

작업 항목:

- [x] Rate Limit fallback 분기 — non-streaming (manage_generator) +
  streaming (_streaming_event_stream) 양쪽 + 회귀 3건 (다음 commit)

### feature16: 운영 라이브 smoke — P3

- **목표**: 본 담당자 영역 모든 fix·통합 완료 후 끝-끝 운영 검증
- **선행 조건**: feature12 ~ feature15 모두 완료
- **시나리오**:
  - docker compose 전체 (Qdrant + MongoDB + MySQL) 기동
  - `.env` 에 `RAG_USE_REAL_ADAPTERS=true` + `RAG_OPENAI_API_KEY`
  - `python scripts/ingest_samples.py --use-mongo-cache` 로 chunk_lookup MongoDB 적재
  - uvicorn 실행 + 다양한 질의 (장애대응 / 운영가이드 / 정책절차 / 이력조회 4종 의도)
  - SSE token streaming 체감 latency 측정 (P95 5초 KPI 확인)
  - Prometheus `/metrics` 수집 확인

작업 항목:

- [x] 운영 smoke 시나리오 실행 + 결과 working-log 기록 — 4종 의도 +
  streaming 1건 + /metrics 수집. 발견 1건 (Prometheus histogram bucket
  협소) 후속 fix. 발견 2~3건 (라우터 의도 오분류 / non-streaming P95) 은
  feature17 / BFF 권고로 이관 (다음 commit)

### feature17: 평가 세션 (F + G) — P3

- **목표**: 설계서 §7 평가 규칙
- **(F) all-sentence evaluation mode 비용/정확도 측정** — 현재 본 어댑터는 suspicious
  only. all-sentence 모드 켰을 때 비용 증가 vs False Negative 감소 측정.
- **(G) agent rule-based 정합 검증** — 본 저장소 `verify_answer_rules` vs agent 의
  `rule_based_verifier` 가 같은 의심 판정을 내리는지 비교
- **추가 평가**:
  - Golden Set 50건 답변 품질 측정 (ROUGE-L / BERTScore)
  - Precision@k / 응답 지연 / 환각 비율

작업 항목:

- [x] **feature17a** — Evaluation Set 골격(10건) + 평가 스크립트
  (scripts/run_evaluation.py) + LLM 커스텀 메트릭 4종 (llm_fallback_total /
  verification_status_total / answer_generation_latency_seconds /
  intent_classification_total) + 회귀 7건 (다음 commit)
- [x] **feature17b** — 완료 (2026-05-26 정리: 평가셋 50건 + Golden Set 자동 추출 실행 확인).
  - [x] scripts/backfill_chunk_ids.py (Qdrant scroll 로 expected_chunk_ids 자동 채움)
  - [x] pyproject.toml evaluation extras (evaluate / rouge-score / bert-score)
  - [x] scripts/run_evaluation.py --rouge-l / --bert-score 옵션 + helper
  - [x] Evaluation Set 50건 라벨링 — 시드 10건 (human) + Claude bootstrap 40건
    (의도 비율 35:30:20:15 + 첨부 활용 8건). 사용자 검수 후 backfill + 평가 완료
    (`samples/evaluation_set.json` 50건).
  - [x] Golden Set 자동 추출 (3 조건 AND 필터) — 실행 완료. `scripts/extract_golden_set.py`
    + 산출물 `reports/golden_set_*.json` (최신 `golden_set_20260520_035803.json`, 50건 중
    4건 추출 / top1≥80 + verification PASS + feedback 통과). 추출 기준이 엄격해 골든셋이
    작은 점은 후속에서 기준 재조정 여지(필요 시).
- [-] **feature17c** — 튜닝 (Pool 가중치 그리드 서치 / 생성기 prompt /
  Cross-Encoder 임계값) — 라우터 prompt 튜닝은 2026-05-19 fix
  (app/query/routing_transport.py) 로 사실상 달성 (정확도 4/4=100%)
  - [x] feature17c-1/2/3 — Cross-Encoder temperature(T=4) + Source.score saturation
    fix (generator 진짜 rerank score 전달)
  - [x] **feature17c-4** — 첨부 청크 인덱싱 wiring (scripts/ingest_samples.py 가
    chunk_attachment 미호출 → 첨부 검색 0건. collect_chunks 헬퍼 분리 + 첨부 청킹
    호출 + 회귀 4건). 사용자 Mac 재적재·재평가 완료: 첨부 청크 51건 적재, 전체
    Precision 68→72% / 환각 37→34% / P95 18.1→13.9초. 단, 첨부 8건은 2차 원인으로
    여전히 대부분 0건 → feature17c-5 로 이어짐.
  - [x] **feature17c-5** — metadata_filters 키 매핑 fix (router 복수형
    space_keys/document_types/source_types → payload 단수형 space_key/doc_type/
    source_type). `_coerce_metadata_filters` 어댑터에서 rename(vendoring 무수정) +
    회귀 4건(in-memory Qdrant end-to-end). **실측: 집계 무변화** (Precision 70~72%
    유지). 코드는 옳으나 키 불일치가 첨부 차단의 실제 주원인은 아니었음. 첨부 개별
    변동은 라우터 LLM 비결정성(동일 코드 2회 실행 상이).
  - [x] **feature17c-6** — metadata filter 0건 fallback 재검색. 정적 분석으로
    첨부 질의 결정적 0건의 근본 원인 확정(n_src=0 ⟺ hybrid_search 후보 0건 ⟺
    라우터 LLM 추출 metadata filter 가 payload 불일치로 must 전부 배제; query_points
    score 하한 없음). `_search_and_fuse` 헬퍼 분리 + 0건 시 ACL 유지·filter 완화
    1회 재검색(회귀 안전: 0건일 때만 동작) + 회귀 2건. 사용자 Mac 재평가로 첨부
    Precision before/after 확인 대기.
  - [x] **feature17c-7** — Qdrant payload 풀텍스트(`text`) 저장. 정적 분석으로
    진짜 recall 병목 확정: n_src=0 은 "검색 0건"이 아니라 "생성기 거부"(검색 후보는
    있으나 정답 청크 미포함 OR 200자 프리뷰만 전달). rerank·generator 가 200자
    프리뷰로만 동작하던 것을 payload 풀텍스트로 해소(generator=Agent 무수정, Pipeline
    한 곳 변경). db-schema §1.2 갱신 + 회귀 3건. **★재적재 필수★** 후 재평가로
    Precision/환각 before-after 확인 대기.
  - [x] **feature17c-8** — 첨부 download_url lookup(Mongo) 실패 graceful degrade.
    rerank 의 download_url 조회가 Mongo 장애를 쿼리 전체 실패로 전파하던 버그 수정
    (UI 부가정보라 실패 시 download_url 없이 진행). 회귀 +1.
  - [x] **17c-7/8 재평가 실측** — **Precision@3 68→80% (KPI 75% 충족 ✅)**, n_src=0
    12→6. 풀텍스트 fix 가 recall 병목 해소를 실증(reports/evaluation_20260520_063441).
  - [x] **feature17c-9** — Pool 가중치 그리드 서치 도구: run_evaluation `--pool-weights`
    오버라이드(라우터 래핑) + `--debug-rerank` 후보 page 분포 테이블 + `_parse_pool_weights`
    회귀 4건. 잔여 recall 4건 진단·튜닝 인프라.
  - [x] **잔여 recall 4건 진단** (EVAL-006/024/032/041) — `--debug-rerank` 실측:
    정답 페이지가 후보엔 있으나(예: 100039 #13, 100028 #4) Cross-Encoder(ms-marco,
    영어) 한국어 변별 부족으로 Top-3 밖. 무관 페이지(Route53)를 정답보다 상위 랭크.
    → 병목은 Pool 가중치가 아니라 reranker 변별력으로 확정.
  - [x] **feature17c-10/12** — 다국어 reranker(bge-v2-m3) 실험 후 **ms-marco 로 원복**.
    bge 가 한국어 변별 우수(EVAL-032 #13→#1)하나 CPU 추론이 느려 KPI #4(P95 8초) 위반.
    Precision@3 는 풀텍스트(17c-7)만으로 이미 80%(목표 충족)라 bge 는 선택적이었음 →
    지연 우선 원복. bge 는 운영 GPU 환경 재검토. device 설정(17c-11)은 유지.
  - [x] **평가 비용 분석** — reranker=로컬(비용 0). full 50건 평가 ≈ $0.5~2/회(추정),
    $134 한도 대비 충분. 진단은 --debug-rerank/route(거의 무료), full 평가는 마일스톤만.
  - [x] **feature17c-13** — 환각 측정 공정화: evaluation_set.json `is_answerable`
    (EVAL-021/046=false, 48건 true) + run_evaluation `_summarize_hallucination` 헬퍼로
    answerable 분리(`not_supported_ratio_answerable` 신설, 전체값 유지) + 회귀 4건.
    baseline 재집계 실측: 전체 39.18% → answerable 38.74%(**약 0.5pp만 감소**) →
    환각의 본질은 답없는 항목이 아니라 answerable 항목의 생성기 보수성 부족임을 데이터로
    확인. 코드 경로 무변경, Precision 헤드라인 80% 유지.
  - [x] **feature17c-14** — 생성기 환각 보수성 guard (어댑터 seam, opt-in). 생성기
    system 프롬프트가 vendoring 안에 하드코딩돼 주입 seam 이 없어, transport 어댑터
    경계(`app/query/openai_transport.py` `_normalize_messages`)에 `CONSERVATIVE_SYSTEM
    _GUARD` 를 덧붙이는 방식으로 강화(vendoring 무수정). `generator_conservative_guard`
    토글 기본 OFF → `.env` 로 A/B(`not_supported_ratio_answerable` 비교 후 채택 결정).
    회귀 +4(transport 2 + deps 2). Agent 담당자 통보 — vendored 프롬프트 보수화는
    Agent 측 정식 경로.
    - [x] **A/B 실측(082545)**: guard ON 시 환각 answerable 38.74→37.44%(1.3pp,
      노이즈 범위) + 미근거 문장 수 오히려 증가 + ROUGE-L 소폭↓ → **guard 미채택,
      기본 OFF 유지**. summary 에 guard 상태 기록 추가(추적성).
    - [x] **진단**: NOT_SUPPORTED 가 44/50 항목·전 의도에 균일 분산 → 병목은 생성기
      prompt 가 아니라 **문장별 검증기(verifier)**. 다음 세션은 문장 단위 진단(토큰/
      cited chunk/2단계 사유) 후 결정.
  - [x] **feature17c-15** — 검증 진단 도구 `--debug-verify`: 단일 질의 풀 파이프라인 후
    문장별 1단계(토큰/미확인토큰/인용청크) + 2단계 raw label/score/reason + 미확인 토큰
    위치분류(in_cited=1단계FP / in_other_topk=citation정밀도 / absent=recall·생성갭)
    출력 + reports JSON. 순수 헬퍼 회귀 +4. 거의 무료(단건). **사용자 Mac 진단 실행 →
    결과 기반 fix 결정**(verifier.py 토큰정규화 / _LABEL_MAP 매핑 / citation / recall).
  - [x] **feature17c-16 (★환각 주원인 fix★)** — debug-verify 4건 진단으로 NOT_SUPPORTED
    과대의 주원인이 verifier 문장분리 off-by-one 확정: 생성기가 마침표 뒤에 붙인 [#N]
    마커가 분리 시 다음 문장으로 떨어져 **첫 문장 인용 유실(cited=[])→NOT_SUPPORTED**.
    `_split_sentences` 가 조각 앞 마커를 직전 문장에 재부착하도록 수정(우리 영역,
    vendoring 무관). 회귀 +3. 기존 테스트는 마침표앞 포맷이라 사각지대였음.
    - [x] **재평가 실측(011314)**: 환각 answerable **38.7→31.1%(−7.6pp)**, 첫 문장 NS
      39→21건. Precision@3 80% 불변. ROUGE-L/intent 변동은 verifier 무관 노이즈.
      **단 31.1% 는 KPI(25%/15%) 여전 미달** → 추가 레버 필요.
  - [x] **feature17c-17** — 차단(blocked) 답변 분리: formatter 가 NOT_SUPPORTED>0.5 로
    차단한 답변(사용자 미노출)의 NS 가 환각 집계에 포함되던 것을 분리. `_summarize
    _hallucination` 에 `not_supported_ratio_delivered`(answerable&not blocked) +
    `blocked_n_items` 추가, result 에 is_blocked 기록. 회귀 +1. 011314 실측: **delivered
    환각 20.1%(28/139)**, 차단 10건. 측정 공정화(코드경로 무변경).
  - [x] **feature17c-18** — 잔존 NS 원인 분해: post-fix delivered 4건 재진단(토큰
    in_other_topk 15 vs absent 1) + `--debug-verify` 에 전체 top-k 2단계 재평가 추가
    (flip→SUPPORTED = 오인용/citation정밀도 vs still = 진짜미근거). 회귀 +1(24 passed).
  - [x] **flip 실측 확정(013454~013719)**: delivered NS **12/12 문장 전부 전체 top-k
    재평가에서 SUPPORTED 로 flip** → 잔존 환각은 100% citation 정밀도(사실은 top-k 안
    존재, 생성기가 #1 만 인용). true 환각 ≈ 0. 도구 flip 비교 대소문자 버그도 fix(24 passed).
    청크 사이즈/오버랩은 병목 아님(recall 정상 입증).
  - [x] **feature17c-19** — 검증 2단계 전체 top-k grounding 토글 구현(opt-in, 기본 OFF):
    config `verifier_full_context_grounding` + `manage_verifier_evaluator(full_context=)`
    (target citations=전체 top-k) + graph/deps 와이어링 + `.env.example`. leniency 검증
    `--debug-leniency`(fabricated 통제 문장 전체 top-k 평가 → PASS/FAIL) + `_leniency
    _verdict`. 회귀 +5.
    - [x] **leniency PASS + A/B 실측(022251)**: 통제 문장 UNSUPPORTED 유지(PASS). full_context
      ON → 환각 answerable 31→**2.07%** / delivered 20→**0.70%**(KPI 15% 대폭 충족), 차단
      10→2, ROUGE-L 0.17→0.24↑. Precision 76%(비결정성). 트레이드오프: 미세 wrong-chunk
      거짓음성 잠재 + citation 정밀도 신호 상실 → 별도 추적/생성기 인용(Agent).
  - [x] **feature17c-20 (요구사항 기준 판단)**: 요구사항정의서/기획서 정독 — 환각 정의가
    "**인용 출처**에 근거하지 않는 비율", FR-009 "문장별 출처 명시", FR-010 "(문장,인용
    청크) 페어 검증 + 미인용→자동 UNSUPPORTED". → **full_context 는 사양 부정합(오인용
    은폐) → 기본 OFF 유지, 내부 진단용으로만**. 사양-정합 환각 = per-cited-chunk:
    delivered ~20%/answerable ~31% (최소25% 통과, 목표15% 미달).
  - [x] **feature17c-21** — citation collapse 위치 확정(map_citations·어댑터 무죄,
    원인=LLM 출력=프롬프트 단일인용 유도) + Agent 요청서 작성
    `docs/ai/agent-request-citation-precision.md`. 진단 근거: 12/12 flip, IAM 4단계
    전부 [#1] 사례. 코드 변경 없음(무료 진단만). prompt_template.py 수정안 라인 명시.
  - [x] **feature17c-22** — FR-009 프롬프트 직접 수정(Agent 담당자 1회 예외 승인):
    `answer_generation_agent/.../prompt_template.py` system prompt 에 다중 인용·미근거
    문장 억제 지침 + 출력 schema 예시 단일→다중 context_id 변경(단일 인용 anchoring 제거).
    회귀 테스트 +1. 검증기·어댑터·코드로직 무변경(사양 정합).
  - [x] **feature17c-22 재평가(074259, full_context OFF)** — instruction 지침 단독 효과
    0 확인: answerable 31.3%/delivered 21.7%/blocked 13(baseline 노이즈 범위), 다중 인용
    emit 0건. 프롬프트 텍스트만으로 GPT-4o 단일 인용 못 바꿈(실측). ※1차(034944)는 .env
    full_context ON 잔존으로 측정 무효(answerable 1.2%는 17c-19 ON 재현).
  - [x] **feature17c-23** — few-shot 예시 보강 후 재평가(075919): 다중 인용 emit 또 0건,
    answerable 34.9% 악화, delivered 12.6%는 차단 13→17 증가 착시(17c-17 함정), P@3·ROUGE-L
    퇴행 → **순효과 음, 실패**.
  - [x] **feature17c-24** — 프롬프트 3종 개입 실패 확정(다중 인용 0건) → 17c-22/23 vendored
    프롬프트 변경 + 회귀 테스트 **전부 롤백**(원본 복구, 새 revert 커밋). 요청서 §5.3을
    **Function Calling tools schema(citations minItems:1) 주 권장으로 승격, Agent 이관**.
  - [x] **feature17c-25** — 생성기 문장별 인용 구조 강제 구현(Agent 권한 위임). vendored
    프롬프트 대신 transport 경계에서 OpenAI Structured Outputs(json_schema, strict) 주입
    (`GROUNDED_CITATION_RESPONSE_FORMAT`, opt-in `RAG_GENERATOR_FORCE_CITATION_SCHEMA`).
    sentences[].citations 문장마다 필수 배열 + 다중 인용 description. transport 회귀 3건.
    한계: strict 가 minItems 미지원이라 빈 배열 valid → 효과는 A/B 재평가로 확인. 측정
    방식 점검: 현 환각 지표는 per-cited(=citation precision)라 표준 faithfulness와 다름 →
    측정 이원화(전체 컨텍스트 faithfulness + citation precision) 별도 권고.
  - [x] **FC schema A/B 재평가(011848 vs 012952)** — FC ON 시 환각 answerable 32.1→32.8%·
    delivered 18.7→22.6%, verification_total 162→244, ROUGE-L 0.172→0.146 → **미채택**.
    생성기 측 인용 교정(프롬프트 3종 + 구조 강제) 전부 실패 실증. 토글 기본 OFF 유지.
  - [x] **feature17c-26 측정 이원화** — 현 "환각률"이 표준 faithfulness 아니라 citation
    precision 임을 확정(인용 강제할수록 환각↑). run_query_with_state + run_evaluation 이원화:
    per-cited(citation precision) 유지 + `unfaithful_*`(전체 top-k=표준 faithfulness) +
    flip 분해(citation_imprecision/true_hallucination) 신설. 회귀 통과.
  - [x] **재평가 실측(020421)** — faithfulness(표준 환각) delivered **0.81%**(1/124, 그 1건도
    미인용)·answerable 1.91%, citation precision delivered 19.4%(오인용 23 + 진짜환각 1).
    **"20~32% 환각"은 95%+가 오인용 아티팩트로 확정. 진짜 환각 ≈0, KPI 도전(8%) 대폭 충족.**
  - [x] **527 v0.3.0 docx 완료** — `구현_결과_보고서_527_RAG검색품질_성능최적화_리포트_v0.3.0.docx`
    (rag 폴더 외부 보관). 최신 평가(`evaluation_20260522_020421`) 반영 — Precision@3 80%,
    faithfulness 0.81%, 측정 이원화, Golden Set, 부수 fix. §4 결론에서 잔여 튜닝을 "후속 이관"으로 정리.
  - 잔여(아래는 v0.3.0 보고서 §4가 명시적으로 owner/타 팀에 **이관·보류**한 항목 — 본 담당자
    능동 코드작업 사실상 종료):
    - [x] **KPI 정의 확정 (2026-05-26)** — 헤드라인 환각 = 표준 faithfulness(delivered 0.81%,
      RAGAS/TruLens 정의), citation precision(19.4%)은 "출처 정밀도" 보조 지표로 분리. 이원 측정
      유지. `docs/rag-pipeline-design.md §10` KPI 표에 측정 방식 명시. 외부 기획서 FR-009/010
      문구 갱신은 권고(설계서·527 v0.3.0 §4 근거).
    - [~] Pool 가중치 그리드 서치 — 도구(`--pool-weights`) ○, **미실행**(eval override 전부 null).
      회귀 baseline(Golden Set) 확보됨. 첨부 P@3는 인덱싱 fix로 이미 12→50% 달성, Pool은 보류.
    - [~] 정책절차 Precision — 라우터 의도 오분류 fix(25→100%)로 일부 개선. 추가 개선은 보류.
    - [~] 생성기 prompt 튜닝(인용 교정) — 프롬프트·스키마·FC 3종 실패 실증·미채택, Agent 이관.
    - [~] non-streaming P95 — streaming/GPU reranker, BFF·인프라 영역 이관.

### feature18: 외부 의존 / 부가 — P3

- **Data Ingestion Agent 책임 협의** — `data-ingestion-agent` / `data-sync-agent` 가
  본 저장소 외부 (백엔드/Data 담당자) 영역인지 합의. `document_analyzer_stub` 처리
  방향 결정 (별도 Agent 패키지 받기 vs 본 저장소 직접 작성)
- ~~feature4-B PDF/CSV 첨부 분할기~~ — ✅ 완료 (2026-05-22, feature4-B로 이동·종결)
- **(D) Function Calling 강제 + (E) 자연어 출처 인용 패턴** — Agent 담당자 영역
  (answer_generation_agent prompt template 갱신 요청)

작업 항목:

- [ ] Data Ingestion Agent 책임 협의 + document_analyzer 방향 확정
- [x] feature4-B PDF/CSV 첨부 분할기 (2026-05-22 완료)
- [ ] (D) Function Calling + (E) 자연어 출처 — Agent 담당자 보고

### feature19: SSE 단계별 status 이벤트 (진행 표시) — P2 ✅ 완료 (2026-05-22, `572b395`)

> **독립 버전 (현재 코드 기준).** feature13 코드 마이그레이션(/ml/query·새 SSE 형식)이나
> BE 합의를 **선행 의존성으로 두지 않는다.** 현재 라우트(`POST /api/v1/rag/query`, 이벤트
> token/sources/verification/meta/done)에 진행 표시용 `status` 이벤트를 *추가*만 한다.
> feature13 코드 마이그레이션이 나중에 반영되면 status 이벤트 data 형식(엔드포인트·필드)을
> 그때 함께 정렬한다 — 본 feature 착수에는 영향 없음.

- **배경**: 프론트가 답변 토큰 전/중 진행 표시(연결→검색→재순위→답변→검증…)를 요청.
  현재 SSE에는 진행 상태 push가 없다 → `status` 이벤트 추가.
- **작업 목표**: SSE에 신규 `status` 이벤트를 추가해 RAG 라이프사이클 단계 진입 시 진행 상태를 push.
- **담당 영역**: RAG Pipeline (`app/api/routes.py`), 문서(`docs/api-spec.md`). 프론트 렌더는 FE 담당.
- **브랜치**: `feat/#?/sse-status-event`

#### status 이벤트 형식

- `event: status` / `data: {"phase": "...", "message": "..."}` (JSON 문자열).
- 송신 위치: 기존 `token`/`sources`/`verification`/`meta`/`done` 이벤트와 함께, 각 phase 진입 시 1회.
- phase 목록은 FE 제시안(connecting … done/error)을 현재 코드 실제 위치에 매핑한다:

  | phase | message(예) | 현재 코드 위치 (송신 가능 시점) |
  |---|---|---|
  | `connecting` | 연결 중이에요 | `query_route` 진입 / `_streaming_event_stream` 첫 yield |
  | `acl_filtering` | 접근 권한을 확인하고 있어요 | `extract_principal` + `build_acl_filter` (query_route) |
  | `checking_history` | 이전 대화를 확인하고 있어요 | `manage_history` 노드 |
  | `routing_query` | 질문 의도를 파악하고 있어요 | `router` 노드 (`manage_router`) |
  | `searching` | 관련 문서를 검색하고 있어요 | `hybrid_search` 노드 |
  | `reranking` | 검색 결과를 추려내고 있어요 | `rerank` 노드 (`cross_encoder_rerank`) |
  | `answering` | 답변을 준비하고 있어요 | `stream_openai_answer` 호출 직전 (프롬프트 구성) |
  | `streaming` | 답변을 작성하고 있어요 | 첫 `token` chunk 송신 시점 (token 루프 진입) |
  | `verifying` | 답변 근거를 검증하고 있어요 | `verify_pipeline_node` (1+2단계) |
  | `formatting` | 답변을 정리하고 있어요 | `format_response` → sources/verification/meta 송신 직전 |
  | `done` | 완료 | 스트림 정상 종료 (기존 `done` 이벤트와 정합 — 아래 결정 참고) |
  | `error` | 오류가 발생했어요 | 처리 실패 시 (아래 결정 참고) |

  - 검색 0건(RETRIEVAL_EMPTY) 분기는 `reranking`/`answering`/`streaming`을 건너뛰고
    `formatting`으로 직행한다(표준 응답). `verifying`은 검증 결과 유무에 따라 생략 가능.

- **결정 필요(소): `done`/`error` phase 처리** — 기존에 `done` SSE 이벤트가 이미 존재하고,
  비-streaming 경로엔 `error` 이벤트가 없다(HTTP 에러 JSON). 택1:
  - (가) `done`/`error`는 기존 `done` 이벤트 + HTTP 에러로 표현하고, `status`는 진행 phase
    (connecting~formatting)만 송신. (최소 변경, 권장)
  - (나) `status:{phase:done}` / `status:{phase:error, message}`도 명시 송신해 FE가 단일
    `status` 스트림만 구독하도록. (스트리밍 중 발생 오류는 status error로 표면화)

#### 구현 (RAG 영역)

- **수정 대상**: `app/api/routes.py` — `_streaming_event_stream`(streaming 경로) +
  `_event_stream`/`_sse_payload`(비-streaming 경로에도 동일 phase 적용 여부 결정).
  `docs/api-spec.md`(status 절 추가). `tests/api/test_query_route.py`(회귀).
- **수정하지 않을 파일**: `app/pipeline/query_graph.py`(그래프 구조 유지),
  `app/schemas/*`, vendoring agent 패키지(무수정), 다른 팀원 담당 영역.
- **기술 제약 / 방식 결정** — phase는 송신 위치에 따라 난이도가 둘로 나뉜다:
  - **즉시 가능(라우트에서 직접 yield)**: `connecting`/`acl_filtering`(graph invoke 전),
    `answering`/`streaming`/`verifying`/`formatting`(graph invoke 후 라우트가 직접 수행).
    → `_streaming_event_stream`에 status yield만 추가하면 됨.
  - **그래프 내부 4단계가 관건**: `checking_history`/`routing_query`/`searching`/`reranking`은
    현재 `streaming_graph.invoke(state)` 단일 블로킹 호출 안에서 실행돼 사이에 끼울 수 없음. 택1:
    - (A) `streaming_graph.astream(state, stream_mode="updates")`로 전환 → 노드 완료 update를
      phase로 변환(노드명 manage_history/router/hybrid_search/rerank → phase 매핑).
    - (B) invoke를 노드 단위로 분할 호출하고 각 노드 전후로 phase를 yield.
  - 절충안: 그래프 내부 4단계는 한 번에 `searching` 1개만 송신하고(invoke 전), 라우트 단계는
    세분화 — FE가 4단계 분리 표시가 꼭 필요한지에 따라 결정.
- **예상 영향 범위**: 기존 5개 이벤트(이름·순서·형식) 무변경, status는 *추가*라 status를 무시하는
  기존 클라이언트도 정상 동작. CLAUDE.md상 SSE 이벤트 추가는 사전 협의·문서 동반.
- **테스트 방법**: streaming 경로에서 phase가 token 이전/중 순서대로 송신되는지 회귀,
  검색 0건 시 phase 단축(reranking~streaming 생략) 회귀, 기존 5개 이벤트 무회귀,
  status 무시 시 token 누적·sources/verification/meta/done 정상.
- **완료 기준**: 회귀 테스트 통과 / `./scripts/verify.sh` 통과 / `docs/api-spec.md` status 절 추가 /
  FE에 phase 목록·형식 핸드오프 / working-log + commit.
- **문서 수정 필요 여부**: 필요 — `docs/api-spec.md`(status 이벤트 절).

**결정 기록 (2026-05-22):**

- **그래프 내부 4단계 세분화 → 절충안 채택.** `checking_history`/`routing_query`/`searching`/
  `reranking`을 분리하지 않고 `searching` 단일 phase로 통합 송신(invoke 직전 1회).
  astream(A)·노드 분할(B)은 미채택 — FE가 4단계 분리 표시를 요구하면 그래프 호출을
  노드 단위 스트리밍으로 전환하는 별도 작업으로 다룬다.
- **`done`/`error` phase → 옵션 (가) 채택.** status로는 진행 phase(connecting~formatting)만
  송신하고, 종료·오류는 기존 `done` 이벤트 + 기존 에러 처리(SSE `error` / HTTP 에러)로 표현.
- **비-streaming 경로 제외.** 단일 블로킹 invoke 후 모든 이벤트를 한꺼번에 flush해 phase가
  동시에 발사 → 진행 표시 가치 없음. streaming 경로(`_streaming_event_stream`)에만 적용.
- 최종 phase 7종: `connecting → acl_filtering → searching → answering → streaming →
  verifying → formatting`.

작업 항목:

- [x] (방식 결정) 그래프 내부 4단계 세분화 → 절충안(searching 단일 통합) 채택, done/error 옵션(가)
- [x] `app/api/routes.py` — 라우트 직접 phase(connecting/acl_filtering/answering/streaming/
  verifying/formatting) + searching(invoke 직전) status yield + 회귀 4건 — `572b395`
- [x] 그래프 내부 4단계 세분화 → 절충안으로 미분리(searching 통합), 별도 작업으로 보류
- [x] `docs/api-spec.md` status 이벤트 절 추가 — `03f9e35`
- [x] FE 핸드오프 — `docs/sse-frontend-contract.md` status 절 정합 (`03f9e35` 생성 / `6b3f124` 모순 수정)

---

## 완료 현황 (2026-05-26 갱신)

- **본 담당자 (Pipeline + Storage) 영역 진척도**: **~100%** (운영성·관측성·streaming
  + Rate Limit fallback + 운영 라이브 smoke + LLM 커스텀 메트릭 + 평가 인프라 완성)
- **완료 (Milestone A·B·C + Agent 통합 3/4 + (B) 운영 transport + (A 인프라) streaming +
  Mode B 시연 검증 + Milestone D feature12 + feature13 PDF #2(/ml/query 마이그레이션) +
  feature14 + feature15 + feature16 + feature17a + feature17b + feature19 SSE status 이벤트)**
- **실 연동(운영 어댑터) 완료**: feature5-B(E5/BM25/Qdrant/Mongo 클라이언트, `633d821`/`2835ccd`)
  + feature9-B(CrossEncoderRerankerImpl + hybrid_search/cross_encoder_rerank 노드, `4f2b0f3`/
  `6e6753e`/`b080bdd`)가 `build_real_deps`로 배선돼 `RAG_USE_REAL_ADAPTERS=true` 운영 경로로
  동작. feature17c 재적재·재평가(`844cd69`, Precision@3 68→80%)로 끝-끝 검증됨.
  (※ 위 두 feature 본문 체크박스가 2026-05-27까지 stale `[ ]` 였던 것을 실제 구현·커밋 사실에
  맞춰 `[x]` 로 정정 — 코드 변경 없음, 문서 정합만.)
- **feature17c**: 엔지니어링·튜닝(17c-1~26) + KPI 달성·확정 완료(Precision@3 80% /
  헤드라인 환각=faithfulness 0.81% 확정, 측정 방식 설계서 §10 명시) + 527 v0.3.0 docx 산출.
  잔여는 전부 타 팀/Agent/인프라 이관·보류 항목 — Pool 가중치 그리드 서치(도구 ○ / 미실행,
  보류), 정책절차 Precision 추가 개선(보류), 생성기 prompt(Agent 이관), non-streaming P95(인프라 이관).
  → **본 담당자 능동 작업 종료(실질 완료).**
- **잔여 (Milestone D)**: feature13 PDF #3(BE ACL 컬럼, 외부 협의) / feature17c 이관·보류분(위) / feature18
- **외부 협의 대기**: feature13 PDF #3 (BE 명세), feature18(Data Agent / Agent 담당자 영역)
