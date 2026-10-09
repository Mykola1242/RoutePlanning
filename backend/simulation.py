"""Deterministic grid simulation, independent of HTTP and presentation."""
from dataclasses import dataclass, field
from copy import deepcopy
from time import perf_counter
import networkx as nx
from .delivery_bot import DeliveryBot
from .missions import MissionProposal, validate_mission
from . import recovery as recovery_agent
from .maps import get_map

# Legacy exports remain available; runtime logic uses each simulation's layout.
_DEFAULT = get_map("north")
WIDTH, HEIGHT = _DEFAULT["width"], _DEFAULT["height"]
SHELVES, STATIC, STATIONS = _DEFAULT["shelves"], _DEFAULT["static"], _DEFAULT["stations"]


@dataclass
class Simulation:
    position: tuple | None = None
    goal: tuple | None = None
    obstacles: set = field(default_factory=set)
    path: list = field(default_factory=list)
    trail: list = field(default_factory=list)
    status: str = "idle"
    steps: int = 0
    plans: int = 0
    planning_ms: float = 0
    events: list = field(default_factory=list)
    event_id: int = 0
    movers: list = field(default_factory=list)
    ticks: int = 0
    bot: DeliveryBot | None = None
    traffic_running: bool = False
    cargo: bool = False
    mission: dict | None = None
    service_left: int = 0
    revision: int = 0
    recovery: dict | None = None
    recovery_calls: int = 0
    recovery_log: list = field(default_factory=list)
    detour_used: bool = False
    map_id: str = "north"

    def __post_init__(self):
        self.layout = get_map(self.map_id)
        self.width, self.height = self.layout["width"], self.layout["height"]
        self.static = self.layout["static"]
        self.stations = self.layout["stations"]
        self.destinations = self.layout["destinations"]
        if self.position is None:
            self.position = self.destinations["base"]["position"]
        if not self.trail:
            self.trail = [self.position]

    def check_mission(self, proposal):
        validate_mission(proposal, self.position, self.cargo, self.obstacles,
                         self.width, self.height, self.static, self.destinations)

    def set_mission(self, proposal, stats):
        self.check_mission(proposal)
        self.mission = {
            **proposal.model_dump(), "status": "draft", "index": 0,
            "stats": stats,
            "allow_base_detour": False,
        } if proposal.outcome == "mission" else None
        self.recovery, self.recovery_calls, self.recovery_log = None, 0, []
        self.detour_used = False
        self.revision += 1
        self.log("План місії готовий до перегляду." if self.mission else proposal.message)

    def begin_mission_step(self):
        mission = self.mission
        if mission["index"] >= len(mission["steps"]):
            mission["status"] = "completed"
            self.status = "arrived"
            self.log("Місію завершено.")
            return
        step = mission["steps"][mission["index"]]
        target = self.destinations[step["station"]]
        self.goal = target["position"]
        if step["action"] == "go_to":
            self.plan()
            if self.status == "ready":
                self.status = "running"
            elif self.status == "blocked":
                self.status = "waiting" if self.movers or self.bot else "blocked"
                if self.status == "blocked":
                    mission["status"] = "blocked"
            elif self.status == "arrived":
                mission["index"] += 1
                self.begin_mission_step()
        else:
            if self.position != self.goal or (step["action"] == "load") == self.cargo:
                mission["status"] = "blocked"
                self.status = "blocked"
                self.log("Не виконані умови операції з вантажем. Місію зупинено.")
                return
            self.path = [self.position]
            self.service_left = self.service_left or 3
            self.status = "servicing"
            operation = "Завантаження" if step["action"] == "load" else "Розвантаження"
            self.log(f"{operation}: {target['name']}.")

    def command(self, action, position=None):
        previous_revision = self.revision
        old_position = self.position
        mission = self.mission
        unfinished = mission and mission["status"] not in ("completed", "cancelled")
        if unfinished and action in ("goal", "plan", "run"):
            raise ValueError("Керуйте місією через її панель або спочатку скасуйте її.")
        if action == "recovery_detour":
            if not mission or mission["status"] != "draft":
                raise ValueError("Дозвіл на від’їзд задається перед запуском місії.")
            mission["allow_base_detour"] = not mission.get("allow_base_detour", False)
            self.revision = previous_revision + 1
            return
        if action == "step" and recovery_agent.needs_tick(self):
            # Keep checking blocked missions even when there are no moving obstacles.
            mission["status"], self.status = "running", "waiting"
        if action == "mission_start":
            if not mission or mission["status"] not in ("draft", "paused", "blocked"):
                raise ValueError("Немає місії для запуску або продовження.")
            remaining = MissionProposal(outcome="mission", message=mission["message"],
                                        steps=mission["steps"][mission["index"]:])
            self.check_mission(remaining)
            if self.recovery:
                self.recovery["status"] = "resolved"
                self.recovery["message"] = "Оператор відновив місію. Виконання продовжується."
            if mission["status"] == "draft":
                self.steps, self.plans, self.planning_ms = 0, 0, 0
                self.trail = [self.position]
            mission["status"] = "running"
            self.log("Виконання місії розпочато.")
            self.begin_mission_step()
        elif action == "mission_cancel":
            if not unfinished:
                raise ValueError("Немає активної місії.")
            mission["status"] = "cancelled"
            self.status, self.goal, self.path = "idle", None, []
            self.service_left = 0
            self.traffic_running = False
            self.log("Місію скасовано. Фактичний стан вантажу збережено.")
        elif action == "pause" and unfinished and mission["status"] in ("running", "blocked", "paused"):
            mission["status"], self.status = "paused", "paused"
            self.traffic_running = False
            self.log("Місію призупинено.")
            if self.recovery:
                self.recovery.update(status="operator", message="Місію призупинив оператор.")
        elif action == "step" and unfinished and mission["status"] == "running" and self.status == "servicing":
            self.ticks += 1
            self.move_obstacles()
            self.move_bot()
            self.service_left -= 1
            if self.service_left == 0:
                self.cargo = mission["steps"][mission["index"]]["action"] == "load"
                self.log("Вантаж завантажено." if self.cargo else "Вантаж доставлено й розвантажено.")
                mission["index"] += 1
                self.begin_mission_step()
        else:
            previous = self.status
            self._command(action, position)
            if action == "goal" and not unfinished:
                self.mission, self.recovery = None, None
                self.recovery_log, self.recovery_calls = [], 0
            if action != "reset" and unfinished:
                if mission["status"] == "running":
                    if previous == "servicing":
                        self.status = "servicing"
                    elif self.status == "arrived":
                        mission["index"] += 1
                        self.begin_mission_step()
                    elif self.status == "blocked":
                        mission["status"] = "blocked"
                    elif self.status == "ready":
                        self.status = "running"
                elif mission["status"] == "paused":
                    self.status = "paused"
        if (action not in ("step", "reset", "mission_cancel", "mission_start", "pause")
                and self.recovery and self.recovery["status"] in ("pending", "requesting")):
            recovery_agent.pause_for_operator(self, "Склад змінився під час рішення агента. Перевірте стан і продовжте місію.")
        recovery_agent.monitor(self, action, old_position)
        self.revision = previous_revision + 1

    def moving_cells(self):
        return {m["position"] for m in self.movers} | ({self.bot.position} if self.bot else set())

    def move_bot(self):
        if self.bot:
            reserved = {self.position}
            if self.status == 'running' and len(self.path) > 1:
                reserved.add(self.path[1])
            self.bot.tick(self.width, self.height, self.static | self.obstacles,
                          {m['position'] for m in self.movers}, reserved, self.stations, self.log)

    def move_obstacles(self):
        """One-cell patrol steps; reverse at walls, wait for other occupants."""
        occupied = self.moving_cells()
        for mover in self.movers:
            old = mover["position"]
            dx, dy = mover["direction"]
            candidate = (old[0] + dx, old[1] + dy)
            if not self.valid(candidate) or candidate in self.static | self.obstacles:
                mover["direction"] = (-dx, -dy)
                candidate = (old[0] - dx, old[1] - dy)
            if (self.valid(candidate) and candidate not in self.static | self.obstacles
                    and candidate not in occupied and candidate != self.position):
                occupied.remove(old)
                occupied.add(candidate)
                mover["position"] = candidate

    def replan_after_change(self, active):
        self.plan()
        if active:
            if self.status == "ready":
                self.status = "running"
            elif self.status == "blocked" and (self.movers or self.bot):
                self.status = "waiting"

    def log(self, message):
        self.event_id += 1
        self.events.insert(0, {"id": self.event_id, "message": message})
        self.events = self.events[:30]

    def valid(self, p):
        return 0 <= p[0] < self.width and 0 <= p[1] < self.height

    def plan(self, quiet=False):
        if self.goal is None:
            raise ValueError("Спочатку виберіть ціль на карті.")
        started = perf_counter()
        graph = nx.grid_2d_graph(self.width, self.height)
        graph.remove_nodes_from(self.static | self.obstacles | self.moving_cells())
        try:
            self.path = nx.astar_path(graph, self.position, self.goal,
                                     heuristic=lambda a, b: abs(a[0]-b[0])+abs(a[1]-b[1]))
            self.status = "ready" if len(self.path) > 1 else "arrived"
            if not quiet:
                self.log(f"Маршрут побудовано: {len(self.path)-1} кроків.")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            self.path = []
            self.status = "blocked"
            if not quiet:
                self.log("Шляху немає. Очікування звільнення проходу або зміни карти.")
        self.planning_ms = round((perf_counter() - started) * 1000, 2)
        self.plans += 1

    def _command(self, action, position=None):
        if action == 'bot':
            if self.bot:
                self.bot = None
                self.traffic_running = False
                self.log('Бота доставки прибрано.')
            else:
                spawn = self.layout["bot_spawn"]
                if spawn in self.obstacles | self.moving_cells() or spawn in (self.position, self.goal):
                    raise ValueError(f'Звільніть клітинку {spawn} для бота.')
                self.bot = DeliveryBot(spawn)
                self.log('Бот доставки доданий. Патрулює робочі станції обраного складу.')
            if self.goal is not None and self.status != 'idle':
                self.replan_after_change(self.status in ('running', 'waiting'))
        elif action == 'traffic':
            if not self.bot:
                raise ValueError('Спочатку додайте бота доставки.')
            self.traffic_running = not self.traffic_running
            self.log('Рух середовища увімкнено.' if self.traffic_running else 'Рух середовища вимкнено.')
        elif action in ("goal", "obstacle", "moving_horizontal", "moving_vertical"):
            if position is None:
                raise ValueError("Потрібні координати клітинки.")
            p = tuple(position)
            if not self.valid(p) or p in self.static:
                raise ValueError("Виберіть вільну клітинку в межах складу.")
            if action.startswith("moving_"):
                active = self.status in ("running", "waiting")
                existing = next((m for m in self.movers if m["position"] == p), None)
                if existing:
                    self.movers.remove(existing)
                    self.log("Рухомий візок прибрано.")
                else:
                    if p in self.obstacles | self.moving_cells() or p in (self.position, self.goal):
                        raise ValueError("Виберіть порожню клітинку для візка.")
                    if len(self.movers) >= 20:
                        raise ValueError("Максимум 20 рухомих візків.")
                    direction = (1, 0) if action == "moving_horizontal" else (0, 1)
                    self.movers.append({"position": p, "direction": direction})
                    self.log(f"Додано рухомий візок у ({p[0]}, {p[1]}).")
                if self.goal is not None and self.status != "idle":
                    self.replan_after_change(active)
            elif action == "goal":
                if p in self.obstacles | self.moving_cells():
                    raise ValueError("Ціль не може бути в перешкоді.")
                self.goal, self.path, self.status = p, [], "idle"
                self.steps, self.plans, self.planning_ms = 0, 0, 0
                self.trail = [self.position]
                self.log(f"Нова ціль: ({p[0]}, {p[1]}).")
            else:
                if p in self.moving_cells():
                    raise ValueError("Цю клітинку займає рухомий візок.")
                if p == self.position or (p == self.goal and p not in self.obstacles):
                    raise ValueError("Не можна перекрити робота або ціль.")
                if p in self.obstacles:
                    self.obstacles.remove(p)
                    self.log(f"Перешкоду ({p[0]}, {p[1]}) прибрано.")
                else:
                    self.obstacles.add(p)
                    self.log(f"Додано перешкоду ({p[0]}, {p[1]}).")
                was_running = self.status in ("running", "waiting")
                if self.goal is not None and self.status != "idle":
                    self.log("Карта змінилася. Перерахунок маршруту.")
                    self.replan_after_change(was_running)
        elif action == "plan":
            self.plan()
        elif action == "run":
            if self.status in ("blocked", "paused") and not self.path and (self.movers or self.bot):
                self.status = "waiting"
                self.log("Очікуємо звільнення проходу. Візки продовжують рух.")
                return
            if self.status not in ("ready", "paused") or len(self.path) < 2:
                raise ValueError("Спочатку побудуйте доступний маршрут.")
            self.status = "running"
            self.log("Рух розпочато.")
        elif action == "pause":
            self.traffic_running = False
            if self.status in ("running", "waiting"):
                self.status = "paused"
                self.log("Рух призупинено.")
        elif action == "step":
            if self.status in ("running", "waiting") or self.traffic_running:
                self.ticks += 1
                self.move_obstacles()
                self.move_bot()
                if self.status not in ('running', 'waiting'):
                    # Invalidate a paused/ready route if the independently moving world changed.
                    if self.path and self.moving_cells().intersection(self.path[1:]):
                        previous = self.status
                        self.plan(quiet=True)
                        if previous == 'paused' and self.status == 'ready':
                            self.status = 'paused'
                    return
                if self.status == "waiting" or self.moving_cells().intersection(self.path[1:]):
                    was_waiting = self.status == "waiting"
                    if not was_waiting:
                        self.log("Візок перекрив маршрут. Зупинка та перепланування.")
                    self.plan(quiet=was_waiting)
                    if self.status == "blocked":
                        self.status = "waiting" if (self.movers or self.bot) else "blocked"
                        return
                    if was_waiting and self.status == "ready":
                        self.log("Прохід доступний. Рух відновлено.")
                    if self.status == "arrived":
                        return
                    self.status = "running"
                self.path.pop(0)
                self.position = self.path[0]
                self.trail.append(self.position)
                self.steps += 1
                if len(self.path) == 1:
                    self.status = "arrived"
                    self.log(f"Цілі досягнуто. Пройдено {self.steps} кроків.")
        elif action == "reset":
            self.__dict__.update(Simulation(map_id=self.map_id).__dict__)
            self.log("Симуляцію скинуто. Робот на базі.")
        else:
            raise ValueError("Невідома команда.")

    def snapshot(self):
        return deepcopy({"width": self.width, "height": self.height, "shelves": self.layout["shelves"],
                "map_id": self.map_id, "map_name": self.layout["name"],
                "map_description": self.layout["description"], "destinations": self.destinations,
                "base": self.destinations["base"]["position"],
                "stations": self.stations, "position": self.position, "goal": self.goal,
                "obstacles": sorted(self.obstacles), "path": self.path,
                "trail": self.trail, "status": self.status, "steps": self.steps,
                "plans": self.plans, "planning_ms": self.planning_ms,
                "movers": self.movers, "ticks": self.ticks,
                "bot": self.bot.snapshot(self.stations) if self.bot else None,
                "traffic_running": self.traffic_running,
                "cargo": self.cargo, "mission": self.mission,
                "service_left": self.service_left,
                "revision": self.revision,
                "recovery": self.recovery, "recovery_calls": self.recovery_calls,
                "recovery_log": self.recovery_log,
                "recovery_needs_tick": recovery_agent.needs_tick(self),
                "events": self.events})
