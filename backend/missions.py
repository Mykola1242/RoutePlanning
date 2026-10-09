"""Mission contract and deterministic checks, independent of the LLM provider."""
from typing import Literal

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field
from .maps import get_map

DESTINATIONS = get_map("north")["destinations"]  # Compatibility for old examples/tests.


class MissionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["go_to", "load", "unload"]
    station: Literal["receiving", "receiving_2", "packing", "packing_2", "dispatch", "base"]


class MissionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["mission", "clarification", "unsupported"]
    message: str = Field(min_length=1, max_length=500)
    steps: list[MissionStep] = Field(max_length=16)


def validate_mission(proposal, position, cargo, obstacles, width, height, static, destinations=None):
    """Simulate action preconditions without moving the real robot.

    Dynamic occupancy is checked during execution, not treated as a permanent wall.
    One anonymous cargo unit can be loaded/unloaded at any non-base station.
    """
    destinations = destinations if destinations is not None else DESTINATIONS
    if proposal.outcome != "mission":
        if proposal.steps:
            raise ValueError("Запит на уточнення не може містити дії.")
        return
    if not proposal.steps:
        raise ValueError("Модель повернула порожню місію.")
    graph = nx.grid_2d_graph(width, height)
    graph.remove_nodes_from(static | obstacles)
    for step in proposal.steps:
        if step.station not in destinations:
            raise ValueError("Цієї станції немає на обраній карті.")
        destination = destinations[step.station]
        target = destination["position"]
        if step.action == "go_to":
            if position not in graph or target not in graph or not nx.has_path(graph, position, target):
                raise ValueError(f"Станція «{destination['name']}» недоступна: змініть перешкоди.")
            position = target
        else:
            if position != target or step.station == "base":
                raise ValueError("Завантаження й розвантаження можливі тільки на поточній робочій станції.")
            if step.action == "load":
                if cargo:
                    raise ValueError("Робот уже має вантаж. Спочатку потрібне розвантаження.")
                cargo = True
            else:
                if not cargo:
                    raise ValueError("Не можна розвантажити порожнього робота.")
                cargo = False


def mission_context(sim):
    return {
        "robot_position": sim.position,
        "carrying_cargo": sim.cargo,
        "map_name": sim.layout["name"],
        "stations": sim.destinations,
        "blocked_stations": [key for key, value in sim.destinations.items()
                             if value["position"] in sim.obstacles],
    }
