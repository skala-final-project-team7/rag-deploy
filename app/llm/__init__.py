"""app.llm — LLM 클라이언트 래퍼 [Agent 인프라].

Agent 컴포넌트가 사용하는 LLM 호출을 한 곳으로 모은다. 모델 라우팅·타임아웃·재시도·
Function Calling 스키마 강제·토큰 비용 로깅을 공통 처리한다.

모델 라우팅 (docs/rag-pipeline-design.md §6):
- GPT-4o      답변 생성기
- GPT-4o-mini 질의 라우터 / 답변 검증 2단계 / 멀티턴 히스토리 관리자 / 문서 분석기

계획 모듈:
- client.py            OpenAI 클라이언트 래퍼 (타임아웃·재시도·Fallback)
- structured_output.py Function Calling 기반 구조화 출력 강제 + 스키마 위반 재시도
- tokenizer.py         tiktoken cl100k_base 토큰 카운팅
"""
