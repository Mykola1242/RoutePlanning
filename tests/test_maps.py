import networkx as nx
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.maps import get_map
from backend.missions import MissionProposal, mission_context
from backend.recovery import RecoveryDecision, apply_decision, decision_context
from backend.simulation import Simulation


@pytest.mark.parametrize('map_id', ['north', 'hub'])
def test_layout_all_stations_reachable_and_routes_inside_bounds(map_id):
    sim = Simulation(map_id=map_id)
    assert all(sim.valid(p) for p in sim.static)
    graph = nx.grid_2d_graph(sim.width, sim.height)
    graph.remove_nodes_from(sim.static)
    for station in sim.stations:
        target = tuple(station['position'])
        sim.command('goal', target)
        sim.command('plan')
        assert len(sim.path) - 1 == nx.shortest_path_length(graph, sim.position, target)
        assert all(sim.valid(p) and p not in sim.static for p in sim.path)
        sim.command('run')
        for _ in range(300):
            if sim.status == 'arrived':
                break
            sim.command('step')
        assert sim.position == target and sim.status == 'arrived'


def test_second_stations_mission_and_reset_stay_on_large_map():
    sim = Simulation(map_id='hub')
    base = sim.position
    sim.set_mission(MissionProposal(outcome='mission', message='Доставка №2.', steps=[
        {'action': 'go_to', 'station': 'receiving_2'},
        {'action': 'load', 'station': 'receiving_2'},
        {'action': 'go_to', 'station': 'packing_2'},
        {'action': 'unload', 'station': 'packing_2'},
        {'action': 'go_to', 'station': 'base'},
    ]), {})
    sim.command('mission_start')
    for _ in range(400):
        sim.command('step')
        if sim.mission['status'] == 'completed':
            break
    assert sim.mission['status'] == 'completed' and not sim.cargo and sim.position == base
    sim.command('reset')
    assert sim.map_id == 'hub' and sim.position == (2, 29) and not sim.mission
    assert sim.snapshot()['base'] == (2, 29)


def test_station_availability_is_map_specific_and_layouts_are_independent():
    small, large = Simulation(), Simulation(map_id='hub')
    proposal = MissionProposal(outcome='mission', message='Другий склад.', steps=[
        {'action': 'go_to', 'station': 'packing_2'},
    ])
    with pytest.raises(ValueError, match='станції'):
        small.set_mission(proposal, {})
    large.set_mission(proposal, {})
    assert mission_context(large)['stations']['packing_2']['position'] == (45, 22)
    assert 'packing_2' not in mission_context(small)['stations']
    large.static.clear()
    assert get_map('hub')['static'] and small.static


def test_large_map_bot_patrol_and_mover_boundary():
    sim = Simulation(map_id='hub')
    sim.command('bot')
    assert sim.bot.position == (3, 15)
    sim.command('traffic')
    for _ in range(500):
        sim.command('step')
        assert sim.valid(sim.bot.position) and sim.bot.position not in sim.static
        if sim.bot.deliveries >= 5:
            break
    assert sim.bot.deliveries >= 5
    sim.command('moving_horizontal', (47, 31))
    sim.command('step')
    assert sim.movers[0]['position'] == (46, 31)


def test_large_map_recovery_uses_its_own_base():
    sim = Simulation(map_id='hub', position=(45, 20), cargo=True)
    sim.set_mission(MissionProposal(outcome='mission', message='Доставка.', steps=[
        {'action': 'go_to', 'station': 'packing_2'},
        {'action': 'unload', 'station': 'packing_2'},
    ]), {})
    sim.command('recovery_detour')
    sim.command('mission_start')
    sim.movers.append({'position': (45, 22), 'direction': (0, 1)})
    sim.recovery = {'status': 'requesting', 'wait_used': True, 'blocked_ticks': 14}
    assert 'detour_base' in decision_context(sim)['allowed_actions']
    apply_decision(sim, RecoveryDecision(action='detour_base', reason='На базу.'), {})
    assert sim.goal == (2, 29) and sim.cargo
    assert sim.mission['steps'][1]['station'] == 'packing_2'


def test_api_map_choice_default_validation_and_session_isolation():
    client = TestClient(app)
    catalog = client.get('/api/maps').json()
    assert {item['id'] for item in catalog} == {'north', 'hub'}
    small = client.post('/api/sessions').json()
    big = client.post('/api/sessions?map_id=hub').json()
    assert small['state']['width'] == 24 and big['state']['width'] == 48
    assert client.post('/api/sessions?map_id=unknown').status_code == 422
    for data, expected in [(small, 400), (big, 200)]:
        response = client.post(f"/api/sessions/{data['id']}/command", json={'action': 'goal', 'position': [47, 31]})
        assert response.status_code == expected
    reset = client.post(f"/api/sessions/{big['id']}/command", json={'action': 'reset'}).json()
    assert reset['map_id'] == 'hub' and reset['width'] == 48
