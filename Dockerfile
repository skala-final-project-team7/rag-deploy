# syntax=docker/dockerfile:1.7

# BuildKit 캐시 친화형 파이썬 기반 이미지로 변경
ARG BASE_IMAGE=python:3.11-slim

# [1] builder: 의존성 레이어만 먼저 구성해 변경이 잦은 소스와 분리
FROM ${BASE_IMAGE} AS builder
WORKDIR /src

ARG INSTALL_EXTRAS=ingestion
ARG RAG_USE_REAL_ADAPTERS=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    RAG_USE_REAL_ADAPTERS=${RAG_USE_REAL_ADAPTERS} \
    PATH="/root/.local/bin:${PATH}"

# [2] git 같은 빌드 의존성만 설치하고 apt 캐시 레이어를 정리해 베이스 크기 축소
RUN --mount=type=cache,target=/var/cache/apt \
    apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# [3] 변경 빈도 낮은 의존성 파일만 먼저 복사해 캐시 적중률 높임
COPY pyproject.toml ./

# [4] pip 캐시 마운트로 wheel/패키지 다운로드 재활용
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir uv \
    && uv pip install --system -e ".[${INSTALL_EXTRAS}]"

# [5] 앱 소스 복사는 마지막에 수행해 의존성 레이어 재사용률 증가
COPY . .

# [6] runtime: 실행 런타임만 남겨 슬림한 배포 이미지 생성
FROM ${BASE_IMAGE} AS runtime
WORKDIR /app

ARG RAG_USE_REAL_ADAPTERS=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RAG_USE_REAL_ADAPTERS=${RAG_USE_REAL_ADAPTERS} \
    RAG_PORT=8000

# [7] 빌더에서 설치된 패키지/바이너리를 통째로 이전해 재설치 불필요
COPY --from=builder /usr/local/lib /usr/local/lib
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /src /app

# [8] non-root 사용자 적용
RUN useradd --create-home --system app \
    && chown -R app:app /app

USER app

EXPOSE 8000

# [9] 쉘 엔트리포인트 제거, exec-form ENTRYPOINT + CMD로 기본 옵션 분리
ENTRYPOINT ["uvicorn"]
CMD ["app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
