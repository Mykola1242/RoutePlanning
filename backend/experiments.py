"""Reproducible paired experiments on the real Simulation executor.

No model is substituted for agent mode. Network failures remain failed trials.
The simulation clock freezes during an API call; API wall time is reported separately.
"""
import argparse
import csv
import io
import json
import platform
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from .simulation import Simulation
from .missions import MissionProposal
from . import recovery

SCENARIOS = {
    "clear": "Вільний прохід",
    "short": "Коротке перекриття",
    "long": "Тривале перекриття",
    "detour": "Перекриття з обходом",
    "permanent": "Недоступна ціль",
}
MODES = {"astar": "A* і очікування", "rules": "A* і правила", "agent": "A* і LLM"}
PROTOCOL = "routeplanning-paired-v1"


class ExperimentSimulation(Simulation):
    def __init__(self, mode, scenario, variant):
        super().__init__(map_id="north")
        self.control_policy = mode
        self.scenario = scenario
        self.variant = variant
        self.planning_total_ms = 0.0
        self.decisions = []
        self.elapsed = 0
        # A dividing wall gives a controlled bottleneck on the existing map.
        self.barrier = {(3, y) for y in range(self.height) if y != 7}
        if scenario == "detour":
            self.barrier.discard((3, 14))
        self.obstacles |= self.barrier
        self.gate_active = False
        self.static_gate = False
        self.trace = []

    def plan(self, quiet=False):
        started = perf_counter()
        super().plan(quiet)
        self.planning_total_ms += (perf_counter() - started) * 1000

    def move_obstacles(self):
        # Scripted occupied cell represents a stopped cart, not a patrol prediction.
        # Occupancy follows the same exogenous schedule in every policy.
        return

    def environment(self, tick):
        self.elapsed = tick
        end = (21 if self.scenario == "short" else 45) + self.variant
        active = self.scenario != "clear" and tick >= 18 and (
            self.scenario == "permanent" or tick < end)
        if active == self.gate_active:
            return
        self.gate_active = active
        if self.scenario == "permanent":
            self.obstacles.add((3, 7))
            self.static_gate = True
        else:
            self.movers = ([{"id": "scripted-cart", "position": (3, 7),
                             "direction": (0, 0)}] if active else [])
        self.log("Сценарій: прохід перекрито." if active else "Сценарій: прохід звільнено.")
        if self.status in ("running", "waiting", "blocked"):
            self.replan_after_change(True)

    def frame(self, waiting_ticks):
        return {"tick": self.elapsed, "position": self.position,
                "path": list(self.path), "steps": self.steps,
                "waiting_ticks": waiting_ticks, "cargo": self.cargo,
                "status": self.status, "mission_index": self.mission["index"],
                "gate_active": self.gate_active,
                "message": self.events[0]["message"] if self.events else ""}


def run_trial(mode, scenario, variant=0, max_ticks=180, decision_provider=None):
    started = perf_counter()
    if mode not in MODES or scenario not in SCENARIOS:
        raise ValueError("Невідомий режим або сценарій.")
    if not 0 <= variant <= 4 or not 1 <= max_ticks <= 500:
        raise ValueError("Варіант має бути 0–4, ліміт тактів 1–500.")
    if mode == "agent" and decision_provider is None:
        from .planner import generate_recovery
        decision_provider = generate_recovery
    sim = ExperimentSimulation(mode, scenario, variant)
    sim.set_mission(MissionProposal(outcome="mission", message="Доставка з приймання на пакування.", steps=[
        {"action": "go_to", "station": "receiving"},
        {"action": "load", "station": "receiving"},
        {"action": "go_to", "station": "packing"},
        {"action": "unload", "station": "packing"},
    ]), {})
    sim.mission["allow_base_detour"] = True
    sim.command("mission_start")
    waiting = servicing = api_ms = calls = 0
    costs = []
    requests = []
    outcome = "timeout"
    error = None
    sim.trace.append(sim.frame(waiting))
    for tick in range(1, max_ticks + 1):
        sim.environment(tick)
        before_position, before_status = sim.position, sim.status
        before_steps = sim.steps
        sim.command("step")
        if before_status == "servicing":
            servicing += 1
        elif sim.position == before_position:
            waiting += 1
        # Collision / unit motion invariants are checked on every tick, not inferred.
        assert sim.position not in sim.static | sim.obstacles | sim.moving_cells()
        assert abs(sim.position[0] - before_position[0]) + abs(sim.position[1] - before_position[1]) <= 1
        assert sim.steps - before_steps in (0, 1)
        if sim.recovery and sim.recovery["status"] == "pending":
            sim.recovery["status"] = "requesting"
            sim.recovery["calls"] += 1
            sim.recovery_calls += 1
            calls += 1
            context = recovery.decision_context(sim)
            call_started = perf_counter()
            try:
                decision, stats = decision_provider(deepcopy(context))
                request_ms = (perf_counter() - call_started) * 1000
                api_ms += request_ms
                if stats.get("cost_usd") is not None:
                    costs.append(stats["cost_usd"])
                requests.append({"tick": tick, "context": context,
                                 "decision": decision.model_dump(), "stats": stats,
                                 "request_ms": round(request_ms, 2)})
                recovery.apply_decision(sim, decision, stats)
            except Exception as exc:
                # Preserve diagnostics without ever serializing keys or request headers.
                from .planner import PlannerError
                if not isinstance(exc, PlannerError):
                    raise
                api_ms += (perf_counter() - call_started) * 1000
                error = str(exc)
                requests.append({"tick": tick, "error": error})
                outcome = "provider_error"
                recovery.pause_for_operator(sim, error)
        sim.trace.append(sim.frame(waiting))
        if error:
            break
        if sim.mission["status"] == "completed":
            outcome = "completed"
            break
        if sim.recovery and sim.recovery["status"] == "operator":
            outcome = "operator_required"
            break
    result = {
        "protocol": PROTOCOL, "mode": mode, "scenario": scenario, "variant": variant,
        "outcome": outcome, "success": outcome == "completed",
        "elapsed_ticks": sim.elapsed, "completion_ticks": sim.elapsed if outcome == "completed" else None,
        "path_steps": sim.steps, "waiting_ticks": waiting, "service_ticks": servicing,
        "planning_calls": sim.plans, "planning_total_ms": round(sim.planning_total_ms, 2),
        "operator_requests": int(outcome == "operator_required"),
        "operator_at_tick": sim.elapsed if outcome == "operator_required" else None,
        "llm_calls": calls, "llm_latency_ms": round(api_ms, 2),
        "llm_cost_usd": round(sum(costs), 8) if len(costs) == calls else None,
        "wall_ms": round((perf_counter() - started) * 1000, 2),
        "error": error, "max_ticks": max_ticks,
    }
    # Identity protects metric definitions against accidental double-counting.
    assert result["elapsed_ticks"] == result["path_steps"] + waiting + servicing
    return {"metrics": result, "trace": sim.trace, "requests": requests,
            "decisions": sim.recovery_log,
            "map": {"width": sim.width, "height": sim.height, "shelves": sim.layout["shelves"],
                    "barrier": sorted(sim.barrier), "destinations": sim.destinations}}


def csv_text(trials):
    stream = io.StringIO(newline="")
    if trials:
        writer = csv.DictWriter(stream, fieldnames=list(trials[0]["metrics"]))
        writer.writeheader()
        writer.writerows(trial["metrics"] for trial in trials)
    return stream.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modes", nargs="+", choices=list(MODES), default=["astar", "rules"])
    parser.add_argument("--scenarios", nargs="+", choices=list(SCENARIOS), default=list(SCENARIOS))
    parser.add_argument("--variants", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--output", type=Path, default=Path("experiments/results"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    trials = []
    for scenario in args.scenarios:
        for variant in range(args.variants):
            for mode in args.modes:
                trial = run_trial(mode, scenario, variant)
                trials.append(trial)
                print(json.dumps(trial["metrics"], ensure_ascii=False), flush=True)
                (args.output / "trials.json").write_text(json.dumps({
                    "protocol": PROTOCOL, "created_utc": datetime.now(timezone.utc).isoformat(),
                    "python": platform.python_version(), "platform": platform.platform(),
                    "trials": trials}, ensure_ascii=False, indent=2), encoding="utf-8")
                (args.output / "metrics.csv").write_text(csv_text(trials), encoding="utf-8-sig")
                if trial["metrics"]["outcome"] == "provider_error":
                    raise SystemExit("Provider failed; saved partial results, no automatic retries.")


if __name__ == "__main__":
    main()
