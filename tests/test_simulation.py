from collections import deque
import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.simulation import Simulation, STATIC, WIDTH, HEIGHT


def bfs_distance(start, goal, blocked):
    queue, seen = deque([(start, 0)]), {start}
    while queue:
        (x, y), distance = queue.popleft()
        if (x, y) == goal:
            return distance
        for p in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
            if 0 <= p[0] < WIDTH and 0 <= p[1] < HEIGHT and p not in blocked and p not in seen:
                seen.add(p)
                queue.append((p, distance+1))


@pytest.mark.parametrize('goal', [(22,14),(22,7),(1,2),(8,5)])
def test_shortest_path_and_arrival(goal):
    sim = Simulation()
    sim.command('goal', goal)
    sim.command('plan')
    assert len(sim.path)-1 == bfs_distance(sim.position, goal, STATIC)
    assert not set(sim.path) & STATIC
    assert all(abs(a[0]-b[0])+abs(a[1]-b[1]) == 1 for a,b in zip(sim.path,sim.path[1:]))
    sim.command('run')
    for _ in range(WIDTH*HEIGHT):
        if sim.status != 'running':
            break
        sim.command('step')
    assert sim.position == goal and sim.status == 'arrived'


def test_dynamic_obstacle_replans_without_collision():
    sim = Simulation()
    sim.command('goal', (22,14))
    sim.command('plan')
    sim.command('run')
    blocked = sim.path[1]
    sim.command('obstacle', blocked)
    assert sim.status == 'running'
    assert blocked not in sim.path and sim.plans == 2
    sim.command('step')
    assert sim.position != blocked


def test_unreachable_goal_and_recovery():
    sim = Simulation(position=(0,0), obstacles={(1,0),(0,1)})
    sim.command('goal',(22,14))
    sim.command('plan')
    assert sim.status == 'blocked' and sim.path == []
    sim.command('obstacle',(1,0))
    assert sim.status == 'ready'


def test_pause_reset_and_same_goal():
    sim = Simulation()
    sim.command('goal',(22,14)); sim.command('plan'); sim.command('run')
    sim.command('pause'); sim.command('step')
    assert sim.position == (1,14) and sim.status == 'paused'
    sim.command('reset')
    assert sim.goal is None and sim.steps == 0 and sim.plans == 0
    sim.command('goal',sim.position); sim.command('plan')
    assert sim.status == 'arrived' and len(sim.path) == 1


def test_api_validation_and_session_isolation():
    client = TestClient(app)
    a = client.post('/api/sessions').json()['id']
    b = client.post('/api/sessions').json()['id']
    def send(key,action,position=None):
        return client.post(f'/api/sessions/{key}/command',json={'action':action,'position':position})
    assert send(a,'goal',[4,3]).status_code == 400
    assert send(a,'goal',[-1,0]).status_code == 400
    assert send(a,'goal',[1.5,0]).status_code == 422
    assert send(a,'goal',[True,0]).status_code == 422
    assert send(a,'run').status_code == 400
    assert send(a,'goal',[22,14]).status_code == 200
    assert send(b,'plan').status_code == 400
    assert send('unknown','plan').status_code == 404
    assert send(a,'obstacle',[22,14]).status_code == 400
    assert send(a,'obstacle',[1,14]).status_code == 400
