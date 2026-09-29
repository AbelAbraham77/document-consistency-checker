"""Validated, configurable exact-alias metric registry; no fuzzy matching."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.normalization.text import clean_text


class RegistryConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1, le=1)
    metrics: dict[str, list[str]]

    @field_validator("metrics")
    @classmethod
    def valid_names(cls, metrics):
        import re
        for name, aliases in metrics.items():
            if not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", name):
                raise ValueError("Canonical metric names must use lowercase snake_case")
            if any(not clean_text(alias) for alias in aliases):
                raise ValueError("Metric aliases must not be blank")
        return metrics


class MetricRegistry:
    def __init__(self, config: RegistryConfiguration):
        self._aliases: dict[str, str] = {}
        for canonical, aliases in config.metrics.items():
            for alias in [canonical, *aliases]:
                key = clean_text(alias)
                existing = self._aliases.get(key)
                if existing is not None and existing != canonical:
                    raise ValueError(f"Conflicting metric alias: {alias!r}")
                self._aliases[key] = canonical

    @classmethod
    def from_file(cls, path: str | Path) -> "MetricRegistry":
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"Duplicate registry key: {key}")
                result[key] = value
            return result
        config = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
        return cls(RegistryConfiguration.model_validate(config))

    def lookup(self, text: str) -> str | None:
        return self._aliases.get(clean_text(text))


DEFAULT_REGISTRY = MetricRegistry.from_file(Path(__file__).with_name("metric_registry.json"))
