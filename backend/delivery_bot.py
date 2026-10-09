"""Deterministic delivery controller. One tick is one discrete movement step."""
from dataclasses import dataclass, field
import networkx as nx


@dataclass
class DeliveryBot:
    position: tuple
    station_index: int = 0
    status: str = "moving"
    path: list = field(default_factory=list)
    service_left: int = 0
    waiting_ticks: int = 0
    deliveries: int = 0
    plans: int = 0

    def tick(self, width, height, static, occupied, reserved, stations, log):
        goal = tuple(stations[self.station_index]['position'])
        if self.status == 'servicing':
            self.service_left -= 1
            if self.service_left == 0:
                self.deliveries += 1
                self.station_index = (self.station_index + 1) % len(stations)
                self.status = 'moving'
                self.path = []
                log(f"Бот: обслуговування завершено. Наступна станція — {stations[self.station_index]['name']}.")
            return
        if self.position == goal:
            self.status, self.service_left, self.waiting_ticks = 'servicing', 3, 0
            self.path = [self.position]
            log(f"Бот: {stations[self.station_index]['name']}, обслуговування 3 такти.")
            return

        # A transient obstruction is given three ticks to clear before detouring.
        blocked = static | occupied | reserved
        invalid = any(p in static for p in self.path[1:])
        if not self.path or invalid or self.waiting_ticks >= 3:
            graph = nx.grid_2d_graph(width, height)
            graph.remove_nodes_from(blocked - {self.position})
            self.plans += 1
            try:
                self.path = nx.astar_path(graph, self.position, goal,
                    heuristic=lambda a, b: abs(a[0]-b[0])+abs(a[1]-b[1]))
                if self.waiting_ticks >= 3:
                    log('Бот: після очікування знайдено обхід.')
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                self.path = []
        if len(self.path) < 2 or self.path[1] in blocked:
            self.waiting_ticks += 1
            if self.status != 'waiting':
                log('Бот: очікує прохід; платформа R-01 має пріоритет.')
            self.status = 'waiting'
            return
        self.position = self.path[1]
        self.path.pop(0)
        self.waiting_ticks = 0
        self.status = 'moving'
        if self.position == goal:
            self.status, self.service_left = 'servicing', 3
            log(f"Бот: {stations[self.station_index]['name']}, обслуговування 3 такти.")

    def snapshot(self, stations):
        return {**self.__dict__, 'target': stations[self.station_index]}
