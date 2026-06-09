"""build_source_adapter — Settings.source_type 기반 어댑터 팩토리 검증 (P1-1)."""

from pathlib import Path

import pytest

from app.adapters import (
    JsonFixtureSourceAdapter,
    UnsupportedSourceTypeError,
    build_source_adapter,
)
from app.config import Settings


def _settings(**overrides: object) -> Settings:
    """`.env` 파일 자동 로드를 끄고 명시 인자만 반영한 ``Settings``를 만든다."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_factory_returns_json_fixture_adapter_by_default() -> None:
    adapter = build_source_adapter(_settings())
    assert isinstance(adapter, JsonFixtureSourceAdapter)


def test_factory_injects_samples_dir_from_settings() -> None:
    settings = _settings(samples_dir="custom_samples")
    adapter = build_source_adapter(settings)
    # Settings.samples_dir이 어댑터에 실제로 흐른다 (P1-1 결함 보완)
    assert isinstance(adapter, JsonFixtureSourceAdapter)
    assert adapter.samples_dir == Path("custom_samples")


def test_factory_rejects_unknown_source_type() -> None:
    with pytest.raises(UnsupportedSourceTypeError):
        build_source_adapter(_settings(source_type="unknown"))


def test_factory_defers_atlassian_until_implemented() -> None:
    # access_token/cloudid 전달 경로 확정 후 구현 (current-plan feature2)
    with pytest.raises(NotImplementedError):
        build_source_adapter(_settings(source_type="atlassian"))
