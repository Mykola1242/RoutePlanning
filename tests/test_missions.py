import importlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.missions import MissionProposal, mission_context
from backend.simulation import Simulation
from backend import planner

api = importlib.import_module("backend.app")


def delivery():
    return MissionProposal(outcome="mission", message="Доставка на пакування й повернення на базу.", steps=[
        {"action": "go_to", "station": "receiving"},
        {"action": "load", "station": "receiving"},
        {"action": "go_to", "station": "packing"},
        {"action": "unload", "station": "packing"},
        {"action": "go_to", "station": "base"},
    ])


def tick_until(sim, condition, limit=500):
    for _ in range(limit):
        if condition():
            return
        sim.command("step")
    pytest.fail(f"Condition not reached: {sim.snapshot()}")


def test_delivery_pause_service_cancel_preserves_cargo():
    sim = Simulation()
    sim.set_mission(delivery(), {})
    assert sim.position == (1, 14) and not sim.cargo
    sim.command("mission_start")
    tick_until(sim, lambda: sim.status == "servicing")
    sim.command("step")
    remaining = sim.service_left
    sim.command("pause")
    for _ in range(5):
        sim.command("step")
    assert sim.service_left == remaining and not sim.cargo
    sim.command("mission_start")
    tick_until(sim, lambda: sim.cargo)
    sim.command("mission_cancel")
    position = sim.position
    sim.command("step")
    assert sim.position == position and sim.cargo
    assert sim.mission["status"] == "cancelled"
    sim.command("reset")
    assert not sim.cargo and sim.mission is None


def test_complete_delivery_with_replanning_and_bot():
    sim = Simulation()
    sim.command("bot")
    sim.set_mission(delivery(), {})
    sim.command("mission_start")
    sim.command("step")
    blocked = sim.path[1]
    sim.command("obstacle", blocked)
    assert sim.position != blocked
    tick_until(sim, lambda: sim.mission["status"] == "completed", 1500)
    assert sim.position == (1, 14) and not sim.cargo
    assert sim.mission["index"] == 5


def test_blocked_mission_recovers_and_pause_survives_map_edit():
    sim = Simulation()
    sim.set_mission(delivery(), {})
    sim.command("mission_start")
    sim.command("pause")
    sim.command("obstacle", (2, 14))
    assert sim.status == "paused" and sim.mission["status"] == "paused"
    sim.command("mission_start")
    for cell in [(0, 14), (1, 13), (1, 15)]:
        sim.command("obstacle", cell)
    assert sim.mission["status"] == "blocked"
    sim.command("obstacle", (1, 13))
    assert sim.mission["status"] == "running"  # A short blockage recovers without an LLM call.
    tick_until(sim, lambda: sim.mission["status"] == "completed")


def test_already_at_station_and_cancelled_load_is_not_committed():
    sim = Simulation(position=(1, 2))
    sim.set_mission(delivery(), {})
    sim.command("mission_start")
    assert sim.status == "servicing"
    sim.command("step")
    sim.command("mission_cancel")
    assert not sim.cargo


@pytest.mark.parametrize("steps", [
    [], [{"action": "unload", "station": "receiving"}],
    [{"action": "load", "station": "base"}],
    [{"action": "go_to", "station": "receiving"}, {"action": "unload", "station": "receiving"}],
    [{"action": "go_to", "station": "receiving"}, {"action": "load", "station": "receiving"}, {"action": "load", "station": "receiving"}],
])
def test_invalid_actions_rejected_before_execution(steps):
    sim = Simulation()
    with pytest.raises(ValueError):
        sim.set_mission(MissionProposal(outcome="mission", message="test", steps=steps), {})
    assert sim.mission is None and sim.position == (1, 14)


def test_schema_and_start_revalidation():
    with pytest.raises(ValidationError):
        MissionProposal(outcome="mission", message="test", steps=[{"action": "go_to", "station": "invented"}])
    sim = Simulation()
    sim.set_mission(delivery(), {})
    sim.command("obstacle", (1, 2))
    with pytest.raises(ValueError):
        sim.command("mission_start")
    with pytest.raises(ValueError):
        sim.command("goal", (0, 0))
    assert sim.mission["status"] == "draft"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "last_plan_request", float("-inf"))
    return TestClient(api.app)


def test_api_preview_execution_and_no_llm_on_ticks(client, monkeypatch):
    calls = []
    def generate(text, context):
        calls.append(text)
        return delivery(), {"model": "test", "input_tokens": 30}
    monkeypatch.setattr(api, "generate_mission", generate)
    key = client.post("/api/sessions").json()["id"]
    result = client.post(f"/api/sessions/{key}/mission", json={"text": "Достав вантаж"})
    assert result.status_code == 200 and result.json()["state"]["mission"]["status"] == "draft"
    assert client.post(f"/api/sessions/{key}/mission", json={"text": "Ще"}).status_code == 429
    url = f"/api/sessions/{key}/command"
    client.post(url, json={"action": "mission_start"})
    for _ in range(150):
        state = client.post(url, json={"action": "step"}).json()
        if state["mission"]["status"] == "completed":
            break
    assert state["mission"]["status"] == "completed" and len(calls) == 1


def test_api_clarification_and_stale_result(client, monkeypatch):
    key = client.post("/api/sessions").json()["id"]
    monkeypatch.setattr(api, "generate_mission", lambda *_: (
        MissionProposal(outcome="clarification", message="Куди доставити?", steps=[]), {}))
    result = client.post(f"/api/sessions/{key}/mission", json={"text": "Достав туди"})
    assert result.status_code == 200 and result.json()["state"]["mission"] is None
    monkeypatch.setattr(api, "last_plan_request", float("-inf"))
    def stale(*args):
        api.sessions[key][0].command("reset")
        return delivery(), {}
    monkeypatch.setattr(api, "generate_mission", stale)
    assert client.post(f"/api/sessions/{key}/mission", json={"text": "Достав"}).status_code == 409


def test_config_no_key_exposure_and_missing_key(client, monkeypatch, tmp_path):
    monkeypatch.setattr(planner, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    key = client.post("/api/sessions").json()["id"]
    assert client.get("/api/planner").json()["configured"] is False
    assert client.post(f"/api/sessions/{key}/mission", json={"text": "Достав"}).status_code == 503
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-test-value")
    response = client.get("/api/planner")
    assert response.json()["configured"] is True and "secret-test-value" not in response.text


def mock_provider(monkeypatch, status=200, body=None, timeout=False):
    requests = []
    real_client = httpx.Client
    def handler(request):
        requests.append(json.loads(request.content))
        if timeout:
            raise httpx.ReadTimeout("private info", request=request)
        return httpx.Response(status, json=body)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-test-key")
    monkeypatch.setattr(planner.httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs))
    return requests


def test_provider_schema_budget_and_statistics(monkeypatch):
    calls = mock_provider(monkeypatch, body={"choices": [{"finish_reason": "stop", "message": {
        "content": delivery().model_dump_json()}}], "usage": {"prompt_tokens": 200, "completion_tokens": 100, "cost": 0.001}})
    result, stats = planner.generate_mission("Достав", mission_context(Simulation()))
    assert result == delivery() and stats["input_tokens"] == 200
    assert calls[0]["max_tokens"] == 1200 and calls[0]["reasoning"]["enabled"] is False
    assert calls[0]["provider"]["require_parameters"] is True


@pytest.mark.parametrize("status", [401, 402, 429, 500])
def test_provider_errors_do_not_leak_or_retry(monkeypatch, status):
    calls = mock_provider(monkeypatch, status, {"error": "secret-provider-detail"})
    with pytest.raises(planner.PlannerError) as error:
        planner.generate_mission("Достав", {})
    assert "secret-provider-detail" not in str(error.value) and len(calls) == 1


@pytest.mark.parametrize("finish,content", [("length", "{}"), ("stop", "not-json"), ("stop", '{"outcome":"mission"}')])
def test_provider_invalid_response_rejected(monkeypatch, finish, content):
    mock_provider(monkeypatch, body={"choices": [{"finish_reason": finish, "message": {"content": content}}]})
    with pytest.raises(planner.PlannerError):
        planner.generate_mission("Достав", {})


def test_provider_timeout_is_bounded_without_retry(monkeypatch):
    calls = mock_provider(monkeypatch, timeout=True)
    with pytest.raises(planner.PlannerError) as error:
        planner.generate_mission("Достав", {})
    assert error.value.status_code == 504 and len(calls) == 1
