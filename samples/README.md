# samples — PoC 목 데이터 / 테스트 픽스처

RAG 파이프라인 개발·테스트용 샘플 데이터. 백엔드/Atlassian 연동 전까지 Ingestion 파이프라인의
입력 소스로 사용하며, 단위·통합 테스트의 픽스처로도 활용한다.

## 구성

| 파일 | 내용 | 형식 |
|---|---|---|
| `confluence_sample_data.json` | Confluence 샘플 57페이지 / 6스페이스(CLOUD·CCC·DEVOPS·ONBOARD·SEC·PROJ) / 8사용자 / 첨부 4건 | Atlassian-Python-API 응답 포맷 |
| `datadog_docs.json` | Datadog 한국어 공식 문서 35페이지 / 1스페이스(DATADOG_KR) | 동일 스키마 (Confluence 호환) |
| `attachments/EKS_운영_상세_매뉴얼_v2.3.docx` | Word — Heading 1/2/3 계층 + 표 6개 (docx 청킹 테스트) | .docx |
| `attachments/신규입사자_온보딩_체크리스트_2026.docx` | Word — Heading + List + 표 1개 (docx 청킹 테스트) | .docx |
| `attachments/모니터링_메트릭_정의서_v1.4.xlsx` | Excel — 6시트, 헤더 행 있음 (멀티시트 직렬화 테스트) | .xlsx |
| `attachments/EKS_노드_월간_사용량_통계_2026Q1.xlsx` | Excel — 4시트, 순수 수치 92행 (행 그룹 분할·컬럼명 동봉 테스트) | .xlsx |

## 사용

- `app/adapters/json_fixture.py`(JsonFixtureSourceAdapter)가 위 JSON을 읽어 표준 PageObject 스트림으로 변환한다.
- 첨부 파일은 `confluence_sample_data.json`의 `attachments[]`가 `filename`으로 참조한다.
  실제 파일은 `attachments/`에 위치하며, `extracted_text`는 비어 있으므로 어댑터/분석기가 추출한다.

## 주의

- 본 데이터는 PoC 목적의 샘플이다. `confluence_sample_data.json`에는 ACL 필드
  (`allowed_groups`/`allowed_users`)가 없다 — `docs/db-schema.md`의 ACL 미해결 사항 참조.
- 실 데이터 연동은 `docs/atlassian-api.md`의 Atlassian REST API를 통한다.
