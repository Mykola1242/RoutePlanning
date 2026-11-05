import importlib
from threading import Event
from time import sleep

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from threading import BoundedSemaphore

from backend.experiments import run_trial, csv_text
from backend.planner import PlannerError
from backend.recovery import rule_decision, RecoveryDecision
from backend.experiment_api import make_router


@pytest.mark.parametrize("scenario", ["clear", "short", "long", "detour", "permanent"])
@pytest.mark.parametrize("mode", ["astar", "rules"])
def test_reproducible_trace_and_metric_partition(mode, scenario):
    a, b = run_trial(mode, scenario), run_trial(mode, scenario)
    assert a["trace"] == b["trace"]
    m = a["metrics"]
    assert m["elapsed_ticks"] == m["path_steps"] + m["waiting_ticks"] + m["service_ticks"]
    assert m["llm_calls"] == 0
    assert m["success"] == (scenario != "permanent")
    assert m["completion_ticks"] is None if scenario == "permanent" else m["completion_ticks"] > 0


def test_unreachable_not_reported_as_success_or_zero_completion():
    a, r = (run_trial(mode, "permanent")["metrics"] for mode in ("astar", "rules"))
    assert a["outcome"] == "timeout" and a["elapsed_ticks"] == 180
    assert r["outcome"] == "operator_required" and r["operator_at_tick"] == 23
    assert a["completion_ticks"] is r["completion_ticks"] is None
    assert r["waiting_ticks"] == 6


def test_long_blockage_detour_is_not_falsely_claimed_shorter():
    a, r = (run_trial(mode, "long")["metrics"] for mode in ("astar", "rules"))
    assert r["completion_ticks"] > a["completion_ticks"]
    assert r["path_steps"] > a["path_steps"]
    assert r["waiting_ticks"] < a["waiting_ticks"]


def test_agent_decisions_are_used_and_provenance_retained():
    seen = []
    def provider(ctx):
        seen.append(ctx)
        return rule_decision(ctx), {"model": "TEST_DOUBLE", "cost_usd": 0}
    result = run_trial("agent", "long", decision_provider=provider)
    assert len(seen) == result["metrics"]["llm_calls"] == 2
    assert [r["decision"]["action"] for r in result["requests"]] == ["wait", "detour_base"]
    assert result["metrics"]["success"]


def test_provider_failure_is_not_replaced_with_rules():
    def fail(ctx):
        raise PlannerError("Test failure", 502)
    result = run_trial("agent", "permanent", decision_provider=fail)
    assert result["metrics"]["outcome"] == "provider_error"
    assert result["metrics"]["llm_calls"] == 1
    assert result["metrics"]["llm_cost_usd"] is None
    assert result["metrics"]["completion_ticks"] is None


def test_disallowed_llm_choice_pauses_instead_of_moving():
    def invalid(ctx):
        return RecoveryDecision(action="detour_base", reason="Test"), {"cost_usd": 0}
    result = run_trial("agent", "permanent", decision_provider=invalid)
    assert result["metrics"]["outcome"] == "operator_required"
    assert result["decisions"][0]["accepted"] is False


def test_csv_preserves_unfinished_and_numeric_metrics():
    text = csv_text([run_trial("rules", "permanent")])
    assert "completion_ticks" in text and "operator_required" in text
    assert "None" not in text


def test_background_api_validation_exclusion_and_download(monkeypatch):
    module = importlib.import_module("backend.experiment_api")
    entered, release = Event(), Event()
    real = module.run_trial
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return real(*args, **kwargs)
    monkeypatch.setattr(module, "run_trial", slow)
    app = FastAPI()
    app.include_router(make_router(BoundedSemaphore(1)))
    with TestClient(app) as client:
        assert client.post("/api/experiments", json={"scenario": "bad"}).status_code == 422
        assert client.post("/api/experiments", json={"scenario": "clear", "variant": 50}).status_code == 422
        result = client.post("/api/experiments", json={"scenario": "clear"})
        key = result.json()["id"]
        assert entered.wait(2)
        try:
            assert client.post("/api/experiments", json={"scenario": "clear"}).status_code == 409
        finally:
            release.set()
        for _ in range(100):
            data = client.get(f"/api/experiments/{key}").json()
            if data["status"] != "running":
                break
            sleep(.02)
        assert data["status"] == "completed"
        assert len(data["trials"]) == 2
        assert "completion_ticks" in client.get(f"/api/experiments/{key}/csv").text
        assert client.get("/api/experiments/missing").status_code == 404
