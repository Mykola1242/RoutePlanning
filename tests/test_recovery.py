import importlib
from copy import deepcopy
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from backend import recovery
from backend.missions import MissionProposal
from backend.planner import PlannerError
from backend.simulation import Simulation

api = importlib.import_module('backend.app')


def mission():
    return MissionProposal(outcome='mission', message='Поїхати на приймання.',
                           steps=[{'action': 'go_to', 'station': 'receiving'}])


def block(sim):
    x, y = sim.position
    for cell in [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)]:
        sim.command('obstacle', cell)


def pending_sim():
    sim = Simulation()
    sim.set_mission(mission(), {})
    sim.command('mission_start')
    block(sim)
    for _ in range(recovery.BLOCKED_TICKS):
        sim.command('step')
    assert sim.recovery['status'] == 'pending'
    return sim


def test_short_blockage_recovers_without_model_and_long_one_stops():
    sim = Simulation()
    sim.set_mission(mission(), {})
    sim.command('mission_start')
    block(sim)
    for _ in range(5):
        assert sim.snapshot()['recovery_needs_tick']
        sim.command('step')
    assert sim.recovery['status'] == 'watching'
    sim.command('obstacle', (1, 13))
    assert sim.status == 'running' and sim.recovery_calls == 0
    sim.command('obstacle', (1, 13))
    for _ in range(6):
        sim.command('step')
    assert sim.status == 'paused' and sim.mission['status'] == 'paused'
    assert not sim.snapshot()['recovery_needs_tick']


def test_no_recovery_for_manual_route_or_operator_pause():
    sim = Simulation()
    sim.command('goal', (1, 2))
    sim.command('plan')
    sim.command('run')
    block(sim)
    for _ in range(20):
        sim.command('step')
    assert sim.recovery is None
    sim = pending_sim()
    sim.command('pause')
    for _ in range(20):
        sim.command('step')
    assert sim.recovery['status'] == 'operator'


def test_wait_is_bounded_and_does_not_duplicate_request_ticket():
    sim = pending_sim()
    sim.recovery['calls'] = sim.recovery_calls = 1
    old_ticket = sim.recovery['ticket']
    recovery.apply_decision(sim, recovery.RecoveryDecision(action='wait', reason='Чекаємо.'), {})
    for _ in range(7):
        sim.command('step')
        assert sim.recovery['status'] == 'waiting'
    sim.command('step')
    assert sim.recovery['status'] == 'pending' and sim.recovery['ticket'] != old_ticket
    assert 'wait' not in recovery.decision_context(sim)['allowed_actions']
    recovery.apply_decision(sim, recovery.RecoveryDecision(action='wait', reason='Знову.'), {})
    assert sim.recovery['status'] == 'operator' and not sim.recovery_log[0]['accepted']


def test_wait_advances_world_and_recovers_without_second_model_call():
    sim = pending_sim()
    recovery.apply_decision(sim, recovery.RecoveryDecision(action='wait', reason='Чекаємо.'), {})
    sim.movers.append({'position': (8, 8), 'direction': (1, 0)})
    sim.command('step')
    assert sim.movers[0]['position'] == (9, 8)
    sim.command('obstacle', (1, 13))
    assert sim.status == 'running' and sim.recovery['status'] == 'resolved'


def test_mission_budget_persists_after_manual_resume():
    sim = pending_sim()
    sim.recovery_calls = recovery.MAX_MISSION_CALLS
    recovery.request_decision(sim)
    assert sim.recovery['status'] == 'operator'
    sim.command('obstacle', (1, 13))
    sim.command('mission_start')
    sim.command('obstacle', (1, 13))
    for _ in range(6):
        sim.command('step')
    assert sim.recovery['status'] == 'operator' and sim.recovery_calls == 3


def test_invalid_retry_and_unauthorized_detour_preserve_tasks():
    for action in ['retry_route', 'detour_base']:
        sim = pending_sim()
        steps = deepcopy(sim.mission['steps'])
        recovery.apply_decision(sim, recovery.RecoveryDecision(action=action, reason='test'), {})
        assert sim.mission['steps'] == steps
        assert sim.status == 'paused' and not sim.recovery_log[0]['accepted']


def test_permitted_detour_preserves_cargo_and_original_tasks():
    sim = Simulation(position=(2, 7), cargo=True)
    proposal = MissionProposal(outcome='mission', message='Доставити.', steps=[
        {'action': 'go_to', 'station': 'packing'},
        {'action': 'unload', 'station': 'packing'},
    ])
    sim.set_mission(proposal, {})
    sim.command('recovery_detour')
    sim.command('mission_start')
    # A dynamic occupant prevents reaching the goal, while base remains reachable.
    sim.movers.append({'position': (22, 7), 'direction': (0, 1)})
    sim.recovery = {'status': 'requesting', 'ticket': 'test', 'calls': 1,
                    'blocked_ticks': 14, 'wait_used': True, 'wait_left': 0, 'message': ''}
    assert 'detour_base' in recovery.decision_context(sim)['allowed_actions']
    recovery.apply_decision(sim, recovery.RecoveryDecision(action='detour_base', reason='Від’їхати.'), {})
    assert sim.cargo and sim.detour_used and sim.goal == (1, 14)
    assert sim.mission['steps'][1:] == proposal.model_dump()['steps']
    assert sim.mission['steps'][0] == {'action': 'go_to', 'station': 'base'}
    for _ in range(200):
        sim.command('step')
        if sim.mission['status'] == 'completed':
            break
    assert sim.mission['status'] == 'completed' and not sim.cargo and sim.position == (22, 7)


def test_retry_route_revalidates_and_resumes():
    sim = pending_sim()
    sim.obstacles.remove((1, 13))
    recovery.apply_decision(sim, recovery.RecoveryDecision(action='retry_route', reason='Прохід є.'), {})
    assert sim.status == 'running' and sim.recovery['status'] == 'resolved'


def test_future_station_blocked_then_operator_removes_pallet_and_completes():
    sim = Simulation()
    sim.set_mission(MissionProposal(outcome='mission', message='Доставка.', steps=[
        {'action': 'go_to', 'station': 'receiving'},
        {'action': 'load', 'station': 'receiving'},
        {'action': 'go_to', 'station': 'packing'},
        {'action': 'unload', 'station': 'packing'},
    ]), {})
    sim.command('mission_start')
    sim.command('obstacle', (22, 7))
    for _ in range(100):
        sim.command('step')
        if sim.recovery and sim.recovery['status'] == 'pending':
            break
    assert sim.recovery['status'] == 'pending' and sim.cargo
    recovery.apply_decision(sim, recovery.RecoveryDecision(action='ask_operator', reason='Звільніть пакування.'), {})
    sim.command('obstacle', (22, 7))
    assert sim.status == 'paused'
    sim.command('mission_start')
    for _ in range(100):
        sim.command('step')
        if sim.mission['status'] == 'completed':
            break
    assert sim.mission['status'] == 'completed' and not sim.cargo


@pytest.fixture
def incident_client():
    client = TestClient(api.app)
    key = client.post('/api/sessions').json()['id']
    sim = pending_sim()
    api.sessions[key][0] = sim
    return client, key, sim


def test_api_duplicate_tickets_spend_once_and_pause_on_operator(incident_client, monkeypatch):
    client, key, sim = incident_client
    calls = []
    def decide(context):
        calls.append(context)
        return recovery.RecoveryDecision(action='ask_operator', reason='Приберіть перешкоду.'), {}
    monkeypatch.setattr(api, 'generate_recovery', decide)
    ticket = sim.recovery['ticket']
    for _ in range(3):
        response = client.post(f'/api/sessions/{key}/recovery', json={'ticket': ticket})
        assert response.status_code == 200
    assert len(calls) == 1 and sim.recovery_calls == 1
    assert sim.recovery['status'] == 'operator'
    assert calls[0]['static_route_exists'] is False


@pytest.mark.parametrize('interruption', ['reset', 'mission_cancel', 'pause', 'step', 'obstacle'])
def test_late_decision_cannot_override_user(incident_client, monkeypatch, interruption):
    client, key, sim = incident_client
    def decide(context):
        sim.command(interruption, (8, 8) if interruption == 'obstacle' else None)
        return recovery.RecoveryDecision(action='wait', reason='Чекаємо.'), {}
    monkeypatch.setattr(api, 'generate_recovery', decide)
    response = client.post(f'/api/sessions/{key}/recovery', json={'ticket': sim.recovery['ticket']})
    assert response.status_code == 200
    assert not sim.recovery or sim.recovery['status'] not in ('waiting', 'requesting')
    assert sim.status not in ('running', 'waiting')


def test_api_error_pauses_without_retry(incident_client, monkeypatch):
    client, key, sim = incident_client
    def fail(context):
        raise PlannerError('OpenRouter timeout', 504)
    monkeypatch.setattr(api, 'generate_recovery', fail)
    ticket = sim.recovery['ticket']
    response = client.post(f'/api/sessions/{key}/recovery', json={'ticket': ticket})
    assert response.status_code == 200 and sim.recovery['status'] == 'operator'
    client.post(f'/api/sessions/{key}/recovery', json={'ticket': ticket})
    assert sim.recovery_calls == 1


def test_inflight_request_does_not_hold_world_lock(incident_client, monkeypatch):
    client, key, sim = incident_client
    entered, release = Event(), Event()
    def slow(context):
        entered.set()
        assert release.wait(5)
        return recovery.RecoveryDecision(action='wait', reason='Чекаємо.'), {}
    monkeypatch.setattr(api, 'generate_recovery', slow)
    responses = []
    ticket = sim.recovery['ticket']
    worker = Thread(target=lambda: responses.append(client.post(f'/api/sessions/{key}/recovery', json={'ticket': ticket})))
    worker.start()
    try:
        assert entered.wait(5)
        duplicate = client.post(f'/api/sessions/{key}/recovery', json={'ticket': ticket})
        assert duplicate.status_code == 200 and sim.recovery_calls == 1
        result = client.post(f'/api/sessions/{key}/command', json={'action': 'mission_cancel'})
        assert result.status_code == 200
    finally:
        release.set()
        worker.join(5)
    assert responses[0].status_code == 200 and sim.mission['status'] == 'cancelled'
