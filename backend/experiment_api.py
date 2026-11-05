"""Bounded background jobs; experiment ticks never depend on browser timers."""
from threading import BoundedSemaphore, Lock, Thread
from time import monotonic
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .experiments import MODES, SCENARIOS, csv_text, run_trial
from .planner import public_config


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Literal["clear", "short", "long", "detour", "permanent"]
    variant: int = Field(default=0, ge=0, le=4)
    include_agent: bool = False


def make_router(planner_slot):
    router = APIRouter(prefix="/api/experiments")
    jobs = {}
    mutex = Lock()
    slot = BoundedSemaphore(1)

    @router.get("")
    def catalog():
        return {"scenarios": SCENARIOS, "modes": MODES,
                "agent_configured": public_config()["configured"]}

    @router.post("")
    def start(payload: ExperimentRequest):
        if payload.include_agent and not public_config()["configured"]:
            raise HTTPException(503, "Для режиму LLM додайте ключ на сервері.")
        if not slot.acquire(blocking=False):
            raise HTTPException(409, "Експеримент уже виконується. Дочекайтеся результату.")
        agent_reserved = False
        if payload.include_agent:
            agent_reserved = planner_slot.acquire(blocking=False)
            if not agent_reserved:
                slot.release()
                raise HTTPException(409, "Модель зайнята іншим запитом.")
        key = str(uuid4())
        with mutex:
            for old in list(jobs):
                if monotonic() - jobs[old]["created"] > 7200:
                    del jobs[old]
            while len(jobs) >= 12:
                del jobs[next(iter(jobs))]
            jobs[key] = {"status": "running", "created": monotonic(), "trials": [], "error": None}

        def work():
            try:
                for mode in (["astar", "rules", "agent"] if payload.include_agent else ["astar", "rules"]):
                    trial = run_trial(mode, payload.scenario, payload.variant)
                    with mutex:
                        jobs[key]["trials"].append(trial)
                with mutex:
                    jobs[key]["status"] = "completed"
            except Exception:
                with mutex:
                    jobs[key].update(status="failed", error="Внутрішня помилка експерименту. Часткові результати збережено.")
            finally:
                if agent_reserved:
                    planner_slot.release()
                slot.release()
        try:
            Thread(target=work, daemon=True).start()
        except Exception:
            if agent_reserved:
                planner_slot.release()
            slot.release()
            with mutex:
                del jobs[key]
            raise
        return {"id": key}

    @router.get("/{key}")
    def read(key: str):
        with mutex:
            if key not in jobs:
                raise HTTPException(404, "Результат більше не зберігається. Повторіть експеримент.")
            return {field: jobs[key][field] for field in ("status", "trials", "error")}

    @router.get("/{key}/csv")
    def download(key: str):
        with mutex:
            if key not in jobs:
                raise HTTPException(404, "Результат не знайдено.")
            content = csv_text(jobs[key]["trials"])
        return Response(content="\ufeff" + content, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="experiment.csv"'})

    return router
