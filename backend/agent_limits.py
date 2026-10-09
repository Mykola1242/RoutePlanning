"""Process-wide rolling request limits. Monetary limits belong to OpenRouter."""
from collections import deque
from math import ceil
from threading import Lock
from time import monotonic


class AgentRequestLimits:
    def __init__(self, per_minute=12, per_hour=100, clock=monotonic):
        self.per_minute = per_minute
        self.per_hour = per_hour
        self.clock = clock
        self.requests = deque()
        self.lock = Lock()

    def reserve(self):
        """Reserve an attempt atomically; return seconds to wait if rejected."""
        with self.lock:
            now = self.clock()
            while self.requests and self.requests[0] <= now - 3600:
                self.requests.popleft()
            recent = [stamp for stamp in self.requests if stamp > now - 60]
            waits = []
            if len(recent) >= self.per_minute:
                waits.append(recent[0] + 60 - now)
            if len(self.requests) >= self.per_hour:
                waits.append(self.requests[0] + 3600 - now)
            if waits:
                return max(1, ceil(max(waits)))
            self.requests.append(now)
            return 0
