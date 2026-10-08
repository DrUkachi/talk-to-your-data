"""Pydantic schema for metrics.yaml. Structural validation only -- cross-checking
metric/dimension references against the actual models (models.py) happens in
registry.py, since that needs the model registry, not just the YAML shape.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class DimensionDef(BaseModel):
    name: str
    type: Literal["categorical", "time"]
    description: str


class MetricDef(BaseModel):
    name: str
    description: str
    model: str
    agg: Literal["sum", "avg", "count", "count_distinct"]
    measure: str
    default_filters: dict[str, list[str]] = Field(default_factory=dict)


class MetricsConfig(BaseModel):
    dimensions: list[DimensionDef]
    metrics: list[MetricDef]


def load_metrics_config(yaml_path: Path) -> MetricsConfig:
    with open(yaml_path) as f:
        raw = yaml.safe_load(f)
    return MetricsConfig.model_validate(raw)
