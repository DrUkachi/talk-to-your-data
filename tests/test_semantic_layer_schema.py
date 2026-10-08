"""Structural validation (pydantic, schema.py) and cross-registry validation
(registry.py) both get exercised here -- a metrics.yaml can be valid YAML shape
but still reference a model/measure/dimension that doesn't exist.
"""

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from talk_to_your_data.semantic_layer.registry import SemanticLayerError, build_registry
from talk_to_your_data.semantic_layer.schema import load_metrics_config


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "metrics.yaml"
    path.write_text(textwrap.dedent(content))
    return path


def test_rejects_metric_missing_required_fields(tmp_path):
    path = _write(
        tmp_path,
        """
        dimensions: []
        metrics:
          - name: broken
            description: missing model/agg/measure
        """,
    )
    with pytest.raises(ValidationError):
        load_metrics_config(path)


def test_rejects_invalid_agg_value(tmp_path):
    path = _write(
        tmp_path,
        """
        dimensions: []
        metrics:
          - name: broken
            description: bad agg
            model: orders
            agg: median
            measure: order_id
        """,
    )
    with pytest.raises(ValidationError):
        load_metrics_config(path)


def test_registry_rejects_unknown_model(tmp_path):
    path = _write(
        tmp_path,
        """
        dimensions: []
        metrics:
          - name: broken
            description: unknown model
            model: no_such_model
            agg: count
            measure: "*"
        """,
    )
    with pytest.raises(SemanticLayerError, match="unknown model"):
        build_registry(path)


def test_registry_rejects_unknown_measure(tmp_path):
    path = _write(
        tmp_path,
        """
        dimensions: []
        metrics:
          - name: broken
            description: unknown measure
            model: orders
            agg: sum
            measure: no_such_column
        """,
    )
    with pytest.raises(SemanticLayerError, match="not defined on model"):
        build_registry(path)


def test_registry_rejects_undocumented_filter_dimension(tmp_path):
    path = _write(
        tmp_path,
        """
        dimensions: []
        metrics:
          - name: broken
            description: filters on an undocumented dimension
            model: orders
            agg: count_distinct
            measure: order_id
            default_filters:
              order_status: [delivered]
        """,
    )
    with pytest.raises(SemanticLayerError, match="dimensions: section"):
        build_registry(path)


def test_real_metrics_yaml_builds_cleanly():
    models, metrics, dimensions = build_registry()
    assert len(metrics) == 9
    assert len(dimensions) == 4
    assert len(models) == 5
