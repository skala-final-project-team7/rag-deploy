"""app.config.Settings — 환경 설정 로딩 검증.

작성자 : 최태성
담당 영역 : rag
"""

import os

import pytest

from app.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolate_rag_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """RAG_ 프리픽스 환경 변수를 모두 제거해 테스트 간 격리.

    개발자 머신의 .env 가 무인자 ``Settings()`` 검증을 오염시키지 않도록 한다
    (코드 리뷰 후속, 2026-05-17).
    """
    for key in [name for name in os.environ if name.startswith("RAG_")]:
        monkeypatch.delenv(key, raising=False)


def _settings_without_env_file() -> Settings:
    """``.env`` 파일 자동 로드를 끄고 ``Settings()``를 생성한다."""
    return Settings(_env_file=None)  # type: ignore[arg-type]


def test_settings_instantiates_with_defaults() -> None:
    # 환경 변수·.env 없이도 기본값으로 생성 가능해야 한다 (로컬 개발 편의)
    settings = _settings_without_env_file()
    assert settings.source_type == "json_fixture"
    assert settings.samples_dir == "samples"
    assert settings.qdrant_port == 6333
    assert settings.llm_answer_model == "gpt-4o"
    assert settings.llm_aux_model == "gpt-4o-mini"
    assert settings.openai_api_key.get_secret_value() == ""
    assert settings.otel_enabled is False
    assert settings.otel_service_name == "rag-deploy"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_QDRANT_PORT", "9999")
    monkeypatch.setenv("RAG_SOURCE_TYPE", "atlassian")
    settings = Settings(_env_file=None)  # type: ignore[arg-type]
    assert settings.qdrant_port == 9999
    assert settings.source_type == "atlassian"


def test_openai_api_key_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_OPENAI_API_KEY", "sk-secret-value")
    settings = Settings(_env_file=None)  # type: ignore[arg-type]
    # 평문 시크릿이 repr/str에 노출되지 않아야 한다
    assert "sk-secret-value" not in repr(settings)
    assert "sk-secret-value" not in str(settings)
    assert settings.openai_api_key.get_secret_value() == "sk-secret-value"


def test_qdrant_api_key_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_QDRANT_API_KEY", "qdrant-secret-value")
    settings = Settings(_env_file=None)  # type: ignore[arg-type]

    assert "qdrant-secret-value" not in repr(settings)
    assert "qdrant-secret-value" not in str(settings)
    assert settings.qdrant_api_key.get_secret_value() == "qdrant-secret-value"


def test_get_settings_returns_settings() -> None:
    assert isinstance(get_settings(), Settings)


def test_settings_use_real_adapters_defaults_false() -> None:
    # build_real_deps 후속(2026-05-18) — 운영 어댑터 토글. 기본값 False(PoC).
    # 미설정 환경에서 무의식적으로 운영 모드가 켜져 모델 다운로드가 발생하지 않도록.
    settings = _settings_without_env_file()
    assert settings.use_real_adapters is False


def test_settings_use_real_adapters_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # RAG_USE_REAL_ADAPTERS=true 일 때만 운영 어댑터 부트스트랩을 켠다.
    monkeypatch.setenv("RAG_USE_REAL_ADAPTERS", "true")
    settings = Settings(_env_file=None)  # type: ignore[arg-type]
    assert settings.use_real_adapters is True


def test_settings_otel_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_OTEL_ENABLED", "true")
    monkeypatch.setenv("RAG_OTEL_SERVICE_NAME", "rag-test")
    monkeypatch.setenv("RAG_OTEL_ENVIRONMENT", "staging")
    monkeypatch.setenv("RAG_OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")

    settings = Settings(_env_file=None)  # type: ignore[arg-type]

    assert settings.otel_enabled is True
    assert settings.otel_service_name == "rag-test"
    assert settings.otel_environment == "staging"
    assert settings.otel_exporter_otlp_endpoint == "http://collector:4317"
