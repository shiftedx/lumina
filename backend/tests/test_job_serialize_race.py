"""A job polled while its worker commits the outcome must not mix the old row's fields with the new state."""
from types import SimpleNamespace

import pytest

from app.routers import media_tools, summaries


@pytest.mark.parametrize("module, name, model", [
    (summaries, "_serialize", summaries.SummaryResponse),
    (media_tools, "_serialize_job", media_tools.AsrJobResponse),
])
def test_state_is_read_before_the_fields(monkeypatch: pytest.MonkeyPatch, module, name, model) -> None:
    row = SimpleNamespace(state="running", error=None)

    def state_of(r):  # the worker commits "failed" + its error between the poll's read and the refresh
        r.state, r.error = "failed", "Job failed"
        return r.state

    seen = {}

    def validate(obj, **_):
        seen.update(error=obj.error)
        return SimpleNamespace(model_copy=lambda update: SimpleNamespace(**{**seen, **update}))

    monkeypatch.setattr(module, "state_of", state_of)
    monkeypatch.setattr(model, "model_validate", validate)
    out = getattr(module, name)(row)
    assert (out.state, out.error) == ("failed", "Job failed")
