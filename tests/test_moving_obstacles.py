import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.simulation import Simulation, STATIC


def prepared():
    sim = Simulation()
    sim.command('goal', (22, 14))
    sim.command('plan')
    return sim


def test_crossing_cart_triggers_replan_and_arrival():
    sim = prepared()
    sim.command('moving_vertical', (8, 13))
    plans = sim.plans
    sim.command('run')
    sim.command('step')
    assert sim.movers[0]['position'] == (8, 14)
    assert sim.plans == plans + 1
    assert (8, 14) not in sim.path
    for _ in range(150):
        sim.command('step')
        assert sim.position not in sim.moving_cells()
        assert not sim.moving_cells() & (STATIC | sim.obstacles)
        if sim.status == 'arrived':
            break
    assert sim.status == 'arrived'


def test_waiting_for_temporarily_occupied_goal_resumes():
    sim = Simulation(position=(0, 0))
    sim.command('goal', (2, 0))
    sim.command('moving_horizontal', (1, 0))
    sim.command('plan')
    sim.command('run')
    sim.command('step')
    assert sim.status == 'waiting' and sim.position == (0, 0)
    sim.command('step')
    assert sim.status == 'running' and sim.position == (1, 0)
    sim.command('step')
    assert sim.status == 'arrived'


def test_pause_freezes_world_and_resume_waiting():
    sim = Simulation(position=(0, 0))
    sim.command('goal', (2, 0))
    sim.command('moving_horizontal', (1, 0))
    sim.command('plan'); sim.command('run'); sim.command('step')
    sim.command('pause')
    old = sim.movers[0]['position']
    sim.command('step')
    assert sim.movers[0]['position'] == old and sim.status == 'paused'
    sim.command('run'); sim.command('step')
    assert sim.status == 'running'
    sim.command('reset')
    assert not sim.movers and sim.ticks == 0


def test_walls_and_pallets_reverse_patrol():
    sim = Simulation()
    sim.command('moving_horizontal', (23, 0))
    sim.move_obstacles()
    assert sim.movers[0]['position'] == (22, 0)
    assert sim.movers[0]['direction'] == (-1, 0)
    sim.obstacles.add((21, 0))
    sim.move_obstacles()
    assert sim.movers[0]['position'] == (23, 0)


def test_no_overlap_or_head_on_swap():
    sim = Simulation(position=(1, 0))
    sim.movers = [{'position': (2, 0), 'direction': (-1, 0)},
                  {'position': (3, 0), 'direction': (-1, 0)}]
    sim.move_obstacles()
    assert [m['position'] for m in sim.movers] == [(2, 0), (3, 0)]
    sim.command('goal', (4, 0)); sim.command('plan'); sim.command('run')
    for _ in range(30):
        old_robot = sim.position
        old_carts = [m['position'] for m in sim.movers]
        sim.command('step')
        assert sim.position not in sim.moving_cells()
        assert len(sim.moving_cells()) == len(sim.movers)
        for old_cart, mover in zip(old_carts, sim.movers):
            assert not (sim.position == old_cart and mover['position'] == old_robot)


def test_edit_validation_and_removal():
    sim = prepared()
    for p in (sim.position, sim.goal, (4, 3), (-1, 0)):
        with pytest.raises(ValueError):
            sim.command('moving_horizontal', p)
    sim.command('moving_vertical', (8, 13))
    with pytest.raises(ValueError):
        sim.command('obstacle', (8, 13))
    sim.command('moving_vertical', (8, 13))
    assert not sim.movers


def test_api_exposes_movers_and_tick_state():
    client = TestClient(app)
    key = client.post('/api/sessions').json()['id']
    response = client.post(f'/api/sessions/{key}/command', json={
        'action': 'moving_vertical', 'position': [8, 13]})
    assert response.status_code == 200
    assert response.json()['movers'] == [{'position': [8, 13], 'direction': [0, 1]}]
