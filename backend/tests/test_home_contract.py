"""Home contract: two client metrics that take only the label "home"."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.media_schemas import ClientMetricSample
from app.services.client_metrics import BUDGETS, METRIC_ORDER


@pytest.mark.parametrize("metric", ["home_first_screen_ms", "home_hero_ms"])
def test_home_metrics_take_the_home_label_only(metric: str) -> None:
    assert ClientMetricSample(metric=metric, label="home", value=640).label == "home"
    for label in ("movies", "Home", ""):
        with pytest.raises(ValidationError):
            ClientMetricSample(metric=metric, label=label, value=640)
    assert metric in METRIC_ORDER


def test_home_budgets_are_warm_p50_and_cold_p95() -> None:
    assert BUDGETS[("home_first_screen_ms", "home")] == (600, 1000)
    assert BUDGETS[("home_hero_ms", "home")] == (600, 1500)
    assert METRIC_ORDER.index("home_first_screen_ms") == METRIC_ORDER.index("detail_hero_ms") + 1
