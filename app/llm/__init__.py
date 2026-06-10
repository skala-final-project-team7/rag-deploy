"""app.llm — (예약 패키지) LLM 클라이언트 공통 래퍼 자리.

당초 LLM 호출 공통화(클라이언트 래퍼·구조화 출력·토큰 카운팅)를 모으려던 자리였으나,
실제 LLM transport 는 각 어댑터 옆에 구현됐다 — 본 패키지는 비어 있으며 import 되지 않는다.

실 구현 위치:
- 답변 생성기 transport      app/query/openai_transport.py (GPT-4o)
- 답변 생성기 SSE streaming  app/query/openai_streaming.py
- 질의 라우터 transport      app/query/routing_transport.py (GPT-4o-mini)
- 대화 제목 생성             app/query/titler.py (GPT-4o-mini)
- 검증 2단계/히스토리        agent 패키지 자체 provider (build_real_deps 에서 주입)

모델 라우팅 정책(GPT-4o=생성기, GPT-4o-mini=라우터·검증·히스토리·분석기·제목)은
docs/rag-pipeline-design.md §6 / app/CLAUDE.md §5 참조.
"""
