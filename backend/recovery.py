"""Bounded incident handling. LLM choices never bypass motion/cargo rules."""
from copy import deepcopy
from typing import Literal
from uuid import uuid4

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field

BLOCKED_TICKS = 6
WAIT_TICKS = 8
MAX_MISSION_CALLS = 3
MAX_INCIDENT_CALLS = 2


class RecoveryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["wait", "retry_route", "ask_operator", "detour_base"]
    reason: str = Field(min_length=1, max_length=300)


def route_available(sim, target, dynamic=True):
    graph = nx.grid_2d_graph(sim.width, sim.height)
    graph.remove_nodes_from(sim.static | sim.obstacles | (sim.moving_cells() if dynamic else set()))
    return sim.position in graph and target in graph and nx.has_path(graph, sim.position, target)


def needs_tick(sim):
    return bool(sim.mission and sim.mission["status"] == "blocked" and sim.recovery
                and sim.recovery["status"] in ("watching", "waiting"))


def pause_for_operator(sim, message):
    if sim.recovery:
        sim.recovery["status"] = "operator"
        sim.recovery["message"] = message
    sim.mission["status"] = "paused"
    sim.status = "paused"
    sim.traffic_running = False
    sim.log(message)


def request_decision(sim):
    incident = sim.recovery
    if sim.recovery_calls >= MAX_MISSION_CALLS or incident["calls"] >= MAX_INCIDENT_CALLS:
        pause_for_operator(sim, "Ліміт звернень агента вичерпано. Звільніть прохід і продовжте місію або скасуйте її.")
        return
    # A fresh ticket for each decision, so a duplicate browser request cannot spend again.
    incident["ticket"] = str(uuid4())
    incident["status"] = "pending"
    incident["message"] = "Тривале блокування. Агент обирає наступну дію."
    sim.mission["status"], sim.status = "paused", "paused"
    sim.traffic_running = False
    sim.log(incident["message"])


def monitor(sim, action, old_position):
    mission = sim.mission
    if not mission or mission["status"] in ("draft", "completed", "cancelled"):
        if sim.recovery and mission and mission["status"] in ("completed", "cancelled"):
            sim.recovery["status"] = "resolved"
            sim.recovery["message"] = ("Місію завершено після обробки блокування."
                                       if mission["status"] == "completed" else "Місію скасовано оператором.")
        return
    if mission["status"] == "paused":
        return
    if sim.recovery and sim.recovery["status"] in ("watching", "waiting"):
        if sim.status == "ready":
            mission["status"] = "running"
            sim.begin_mission_step()
        if sim.position != old_position or sim.status in ("running", "servicing", "arrived"):
            sim.recovery["status"] = "resolved"
            sim.recovery["message"] = "Прохід доступний. Виконання місії відновлено."
            sim.log(sim.recovery["message"])
            return
    step = mission["steps"][mission["index"]]
    if step["action"] != "go_to" or sim.status not in ("waiting", "blocked"):
        return
    if not sim.recovery or sim.recovery["status"] == "resolved":
        sim.recovery = {
            "status": "watching", "ticket": None, "blocked_ticks": 0,
            "calls": 0, "wait_used": False, "wait_left": 0,
            "message": "Прохід перекритий. Перевіряємо, чи звільниться він сам.",
        }
    if action != "step":
        return
    sim.recovery["blocked_ticks"] += 1
    if sim.recovery["status"] == "waiting":
        sim.recovery["wait_left"] -= 1
        if sim.recovery["wait_left"] <= 0:
            request_decision(sim)
    elif sim.recovery["blocked_ticks"] >= BLOCKED_TICKS:
        request_decision(sim)


def decision_context(sim):
    mission = sim.mission
    step = mission["steps"][mission["index"]]
    target = sim.destinations[step["station"]]["position"]
    static_route = route_available(sim, target, dynamic=False)
    current_route = route_available(sim, target)
    base = sim.destinations["base"]["position"]
    allowed = ["ask_operator"]
    if not sim.recovery["wait_used"]:
        allowed.append("wait")
    if current_route:
        allowed.append("retry_route")
    if (mission.get("allow_base_detour", False) and not sim.detour_used
            and static_route and not current_route and base not in (target, sim.position)
            and len(mission["steps"]) < 16 and route_available(sim, base)):
        allowed.append("detour_base")
    return {
        "position": sim.position, "carrying_cargo": sim.cargo,
        "map_name": sim.layout["name"], "stations": sim.destinations,
        "target": step["station"], "remaining_steps": deepcopy(mission["steps"][mission["index"]:]),
        "static_route_exists": static_route, "route_available_now": current_route,
        "moving_obstacles": len(sim.movers) + int(sim.bot is not None),
        "blocked_ticks": sim.recovery["blocked_ticks"],
        "wait_already_tried": sim.recovery["wait_used"],
        "allowed_actions": allowed, "wait_duration_ticks": WAIT_TICKS,
    }


def apply_decision(sim, decision, stats):
    allowed = decision_context(sim)["allowed_actions"]
    accepted = decision.action in allowed
    sim.recovery_log.insert(0, {
        "action": decision.action, "reason": decision.reason, "accepted": accepted,
        "stats": stats, "tick": sim.ticks,
    })
    sim.recovery_log = sim.recovery_log[:8]
    if not accepted:
        pause_for_operator(sim, "Рішення агента не відповідає дозволам або стану складу. Потрібне рішення оператора.")
        return
    sim.recovery["message"] = decision.reason
    sim.log(f"Агент: {decision.reason}")
    if decision.action == "ask_operator":
        pause_for_operator(sim, decision.reason + " Змініть карту й продовжте місію або скасуйте її.")
    elif decision.action == "wait":
        sim.recovery.update(status="waiting", wait_used=True, wait_left=WAIT_TICKS)
        sim.mission["status"], sim.status = "running", "waiting"
        sim.log(f"Очікування до {WAIT_TICKS} тактів; середовище продовжує рух.")
    else:
        if decision.action == "detour_base":
            sim.mission["steps"].insert(sim.mission["index"], {"action": "go_to", "station": "base"})
            sim.detour_used = True
            sim.log("Додано дозволений від’їзд на базу. Початкові завдання й вантаж збережено.")
        sim.recovery["status"] = "resolved"
        sim.mission["status"] = "running"
        sim.begin_mission_step()
