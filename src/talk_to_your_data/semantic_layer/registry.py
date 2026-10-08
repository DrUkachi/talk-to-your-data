"""Combines models.py (code) with metrics.yaml (config) into validated lookup
tables, cross-checking references that pydantic alone can't (it doesn't know about
the model registry): does the metric's model exist, does its measure exist on that
model, is every filterable dimension documented in the `dimensions:` section.
"""

from pathlib import Path

from .models import MODELS, Model
from .schema import DimensionDef, MetricDef, load_metrics_config

METRICS_YAML_PATH = Path(__file__).parent / "metrics.yaml"


class SemanticLayerError(ValueError):
    """Raised when metrics.yaml is structurally valid YAML but references something
    that doesn't exist in the model registry."""


def build_registry(
    yaml_path: Path = METRICS_YAML_PATH,
) -> tuple[dict[str, Model], dict[str, MetricDef], dict[str, DimensionDef]]:
    config = load_metrics_config(yaml_path)
    dimensions = {d.name: d for d in config.dimensions}

    metrics: dict[str, MetricDef] = {}
    for m in config.metrics:
        if m.model not in MODELS:
            raise SemanticLayerError(f"metric '{m.name}': unknown model '{m.model}'")
        model = MODELS[m.model]

        if m.measure == "*":
            if m.agg != "count":
                raise SemanticLayerError(
                    f"metric '{m.name}': measure '*' only valid with agg=count"
                )
        elif m.measure not in model.measures:
            raise SemanticLayerError(
                f"metric '{m.name}': measure '{m.measure}' not defined on model '{m.model}'"
            )

        for dim_name in m.default_filters:
            if dim_name not in model.dimensions:
                raise SemanticLayerError(
                    f"metric '{m.name}': default_filters references dimension "
                    f"'{dim_name}', which model '{m.model}' doesn't have"
                )
            if dim_name not in dimensions:
                raise SemanticLayerError(
                    f"metric '{m.name}': dimension '{dim_name}' has no entry in "
                    "the dimensions: section of metrics.yaml"
                )

        metrics[m.name] = m

    used_dims = {dim for model in MODELS.values() for dim in model.dimensions}
    undocumented = used_dims - dimensions.keys()
    if undocumented:
        raise SemanticLayerError(
            f"dimensions used by models but undocumented in metrics.yaml: {sorted(undocumented)}"
        )

    return MODELS, metrics, dimensions


MODELS_REGISTRY, METRICS, DIMENSIONS = build_registry()
