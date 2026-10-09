from pathlib import Path
from threading import Lock, BoundedSemaphore
from time import monotonic
from uuid import uuid4
from typing import Literal
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictInt, Field
from .simulation import Simulation
from .missions import mission_context
from .planner import generate_mission, generate_recovery, public_config, PlannerError
from . import recovery as recovery_agent
from .maps import map_catalog

app = FastAPI(title="RoutePlanning", version="0.6.0")
sessions = {}
lock = Lock()
planner_slot = BoundedSemaphore(1)
last_plan_request = float("-inf")


class Command(BaseModel):
    action: Literal["goal", "obstacle", "moving_horizontal", "moving_vertical", "bot", "traffic", "plan", "run", "pause", "step", "reset", "mission_start", "mission_cancel", "recovery_detour"]
    position: tuple[StrictInt, StrictInt] | None = None


class MissionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1500)


class RecoveryRequest(BaseModel):
    ticket: str = Field(min_length=1, max_length=64)


@app.post("/api/sessions/{key}/recovery")
def recover_mission(key: str, payload: RecoveryRequest):
    with lock:
        if key not in sessions:
            raise HTTPException(404, "Сесію завершено. Оновіть сторінку.")
        sim = sessions[key][0]
        incident = sim.recovery
        if not incident or incident["ticket"] != payload.ticket or incident["status"] != "pending":
            # Repeated/stale tickets are idempotent and never call the model again.
            return sim.snapshot()
        if (sim.recovery_calls >= recovery_agent.MAX_MISSION_CALLS
                or incident["calls"] >= recovery_agent.MAX_INCIDENT_CALLS):
            recovery_agent.pause_for_operator(sim, "Ліміт звернень агента вичерпано. Потрібне рішення оператора.")
            sim.revision += 1
            return sim.snapshot()
        if not planner_slot.acquire(blocking=False):
            recovery_agent.pause_for_operator(sim, "Модель зайнята іншим запитом. Місію призупинено для оператора.")
            sim.revision += 1
            return sim.snapshot()
        incident["status"] = "requesting"
        incident["calls"] += 1
        sim.recovery_calls += 1
        sim.revision += 1
        revision = sim.revision
        sessions[key][1] = monotonic()
        context = recovery_agent.decision_context(sim)
    try:
        failure = None
        try:
            decision, stats = generate_recovery(context)
        except PlannerError as error:
            failure = str(error)
        with lock:
            if key not in sessions or sessions[key][0] is not sim:
                raise HTTPException(409, "Сесію змінено. Рішення агента не застосоване.")
            if (sim.revision != revision or sim.recovery is not incident
                    or incident["status"] != "requesting"):
                # Operator changes, cancellation and reset take priority over late results.
                if sim.recovery is incident and incident["status"] == "requesting":
                    recovery_agent.pause_for_operator(sim, "Стан змінився. Запізніле рішення агента відхилено; продовжте місію вручну.")
                    sim.revision += 1
                return sim.snapshot()
            if failure:
                recovery_agent.pause_for_operator(sim, failure + " Місію призупинено; автоматичного повтору немає.")
            else:
                recovery_agent.apply_decision(sim, decision, stats)
            sim.revision += 1
            return sim.snapshot()
    finally:
        planner_slot.release()


@app.get("/api/planner")
def planner_config():
    return public_config()


@app.post("/api/sessions/{key}/mission")
def propose_mission(key: str, payload: MissionRequest):
    global last_plan_request
    if not payload.text.strip():
        raise HTTPException(400, "Введіть команду для робота.")
    if not planner_slot.acquire(blocking=False):
        raise HTTPException(409, "Планувальник уже обробляє запит. Дочекайтеся відповіді.")
    try:
        with lock:
            if key not in sessions:
                raise HTTPException(404, "Сесію завершено. Оновіть сторінку.")
            sim = sessions[key][0]
            if sim.status in ("running", "waiting", "servicing") or sim.traffic_running:
                raise HTTPException(409, "Спочатку призупиніть рух середовища й робота.")
            if sim.mission and sim.mission["status"] not in ("draft", "completed", "cancelled"):
                raise HTTPException(409, "Спочатку завершіть або скасуйте поточну місію.")
            if monotonic() - last_plan_request < 3:
                raise HTTPException(429, "Зачекайте кілька секунд перед наступною командою.")
            last_plan_request = monotonic()
            sessions[key][1] = monotonic()
            revision, context = sim.revision, mission_context(sim)
        # Do not hold the simulation lock during a network request.
        proposal, stats = generate_mission(payload.text.strip(), context)
        with lock:
            if key not in sessions or sessions[key][0] is not sim or sim.revision != revision:
                raise HTTPException(409, "Стан складу змінився під час планування. Складіть план повторно.")
            sim.set_mission(proposal, stats)
            return {"state": sim.snapshot(), "proposal": proposal.model_dump(), "stats": stats}
    except PlannerError as error:
        raise HTTPException(error.status_code, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    finally:
        planner_slot.release()


@app.post("/api/sessions")
def create_session(map_id: Literal["north", "hub"] = "north"):
    with lock:
        now = monotonic()
        for key in list(sessions):
            if now - sessions[key][1] > 7200:
                del sessions[key]
        if len(sessions) >= 100:
            raise HTTPException(503, "Забагато відкритих сесій.")
        key, sim = str(uuid4()), Simulation(map_id=map_id)
        sim.log("Склад завантажено. Виберіть ціль на карті.")
        sessions[key] = [sim, now]
        return {"id": key, "state": sim.snapshot()}


@app.get("/api/maps")
def maps():
    return map_catalog()


@app.post("/api/sessions/{key}/command")
def command(key: str, payload: Command):
    with lock:
        if key not in sessions:
            raise HTTPException(404, "Сесію завершено. Оновіть сторінку.")
        sim = sessions[key][0]
        sessions[key][1] = monotonic()
        try:
            sim.command(payload.action, payload.position)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return sim.snapshot()


dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if dist.is_dir():
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
