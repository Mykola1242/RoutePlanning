"""Warehouse layouts. Each simulation owns its state, never edits these templates."""
from copy import deepcopy

MAPS = {
    "north": {
        "id": "north", "name": "Склад «Північний»", "width": 24, "height": 16,
        "description": "Навчальний склад: 6 стелажів і 3 станції.",
        "shelves": [(x, y, 3, 4) for y in (3, 9) for x in (4, 10, 16)],
        "destinations": {
            "receiving": {"name": "Приймання", "position": (1, 2)},
            "packing": {"name": "Пакування", "position": (22, 7)},
            "dispatch": {"name": "Відвантаження", "position": (22, 14)},
            "base": {"name": "База", "position": (1, 14)},
        },
        "bot_spawn": (2, 7),
    },
    "hub": {
        "id": "hub", "name": "Логістичний центр «Східний»", "width": 48, "height": 32,
        "description": "Великий склад: 24 стелажі, 5 станцій, вузькі й широкі проходи.",
        "shelves": [(x, y, 4, 5) for y in (3, 10, 18, 25) for x in (6, 12, 19, 26, 33, 39)],
        "destinations": {
            "receiving": {"name": "Приймання №1", "position": (2, 3)},
            "receiving_2": {"name": "Приймання №2", "position": (2, 20)},
            "packing": {"name": "Пакування №1", "position": (45, 8)},
            "packing_2": {"name": "Пакування №2", "position": (45, 22)},
            "dispatch": {"name": "Відвантаження", "position": (45, 29)},
            "base": {"name": "База", "position": (2, 29)},
        },
        "bot_spawn": (3, 15),
    },
}


def get_map(map_id):
    if map_id not in MAPS:
        raise ValueError("Невідома карта.")
    result = deepcopy(MAPS[map_id])
    result["static"] = {(x + dx, y + dy) for x, y, w, h in result["shelves"]
                        for dx in range(w) for dy in range(h)}
    result["stations"] = [{"id": key, **value} for key, value in result["destinations"].items() if key != "base"]
    return result


def map_catalog():
    return [{key: value[key] for key in ("id", "name", "width", "height", "description")}
            for value in MAPS.values()]
