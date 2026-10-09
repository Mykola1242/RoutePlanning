from concurrent.futures import ThreadPoolExecutor
import importlib

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import planner
from backend.agent_limits import AgentRequestLimits


def test_rolling_windows_and_rejected_requests_do_not_extend_wait():
    now = [0.0]
    limits = AgentRequestLimits(per_minute=2, per_hour=3, clock=lambda: now[0])
    assert limits.reserve() == 0
    assert limits.reserve() == 0
    now[0] = 10
    assert limits.reserve() == 50
    now[0] = 60
    assert limits.reserve() == 0
    assert limits.reserve() == 3540
    now[0] = 3600
    assert limits.reserve() == 0


def test_parallel_requests_cannot_overbook_quota():
    limits = AgentRequestLimits(clock=lambda: 0)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: limits.reserve(), range(50)))
    assert results.count(0) == 12
    assert results.count(60) == 38


@pytest.fixture
def provider(monkeypatch):
    calls = []
    response_status = [200]
    real_client = httpx.Client

    def handler(request):
        calls.append(request)
        return httpx.Response(response_status[0], json={
            "choices": [{"finish_reason": "stop", "message": {
                "content": '{"outcome":"clarification","message":"Уточніть станцію","steps":[]}'}}]})

    monkeypatch.setattr(planner, "settings", lambda: ("fake-test-key", "test-model"))
    monkeypatch.setattr(planner, "agent_request_limits", AgentRequestLimits(
        per_minute=1, per_hour=2, clock=lambda: 0))
    monkeypatch.setattr(planner.httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs))
    return calls, response_status


def test_mission_and_recovery_share_provider_quota(provider):
    calls, _ = provider
    planner.generate_mission("На пакування", {})
    with pytest.raises(planner.PlannerError) as error:
        planner.generate_recovery({})
    assert error.value.status_code == 429
    assert len(calls) == 1


def test_provider_failure_counts_and_budget_error_is_clear(provider):
    calls, status = provider
    status[0] = 402
    with pytest.raises(planner.PlannerError) as error:
        planner.generate_mission("На пакування", {})
    assert error.value.status_code == 402
    assert "Бюджет" in str(error.value)
    with pytest.raises(planner.PlannerError) as second:
        planner.generate_mission("Повтор", {})
    assert second.value.status_code == 429
    assert len(calls) == 1


def test_new_session_cannot_bypass_quota_and_manual_simulation_works(provider, monkeypatch):
    api = importlib.import_module("backend.app")
    monkeypatch.setattr(api, "sessions", {})
    monkeypatch.setattr(api, "last_plan_request", float("-inf"))
    with TestClient(api.app) as client:
        first = client.post("/api/sessions").json()["id"]
        assert client.post(f"/api/sessions/{first}/mission", json={"text": "Пакування"}).status_code == 200
        monkeypatch.setattr(api, "last_plan_request", float("-inf"))
        second = client.post("/api/sessions").json()["id"]
        response = client.post(f"/api/sessions/{second}/mission", json={"text": "Пакування"})
        assert response.status_code == 429
        assert "спільний ліміт" in response.json()["detail"]
        assert client.post(f"/api/sessions/{second}/command", json={"action": "goal", "position": [2, 14]}).status_code == 200
        assert client.post(f"/api/sessions/{second}/command", json={"action": "plan"}).status_code == 200
        assert client.post(f"/api/sessions/{second}/command", json={"action": "run"}).status_code == 200
        assert client.post(f"/api/sessions/{second}/command", json={"action": "step"}).json()["position"] == [2, 14]
    assert len(provider[0]) == 1


def test_oversized_command_never_calls_provider(provider):
    api = importlib.import_module("backend.app")
    with TestClient(api.app) as client:
        response = client.post("/api/sessions/unused/mission", json={"text": "а" * 1501})
    assert response.status_code == 422
    assert provider[0] == []
