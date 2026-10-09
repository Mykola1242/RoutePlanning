from backend.simulation import Simulation, STATIC


def tick_checked(sim, count):
    for _ in range(count):
        old_robot = sim.position
        old_bot = sim.bot.position
        sim.command('step')
        assert sim.bot.position != sim.position
        assert sim.bot.position not in STATIC | sim.obstacles
        assert sim.bot.position not in {m['position'] for m in sim.movers}
        assert not (sim.bot.position == old_robot and sim.position == old_bot)


def test_bot_cycles_stations_without_robot_mission():
    sim = Simulation()
    sim.command('bot'); sim.command('traffic')
    tick_checked(sim, 180)
    assert sim.bot.deliveries >= 3
    assert sim.position == (1, 14)


def test_service_takes_three_ticks():
    sim = Simulation()
    sim.command('bot')
    sim.bot.position = (1, 2)
    sim.command('traffic'); sim.command('step')
    assert sim.bot.service_left == 3
    for left in (2, 1):
        sim.command('step')
        assert sim.bot.service_left == left and sim.bot.deliveries == 0
    sim.command('step')
    assert sim.bot.deliveries == 1 and sim.bot.station_index == 1


def test_robot_priority_and_no_collisions():
    sim = Simulation()
    sim.command('bot'); sim.command('goal', (22, 7)); sim.command('plan'); sim.command('run')
    tick_checked(sim, 150)
    assert sim.status == 'arrived'


def test_blocked_bot_recovers_and_pause_freezes():
    sim = Simulation()
    sim.command('bot')
    sim.obstacles.update({(1, 7), (3, 7), (2, 6), (2, 8)})
    sim.command('traffic'); tick_checked(sim, 5)
    assert sim.bot.status == 'waiting'
    sim.command('pause'); position = sim.bot.position
    sim.command('step')
    assert sim.bot.position == position and not sim.traffic_running
    sim.command('obstacle', (2, 6)); sim.command('traffic')
    tick_checked(sim, 30)
    assert sim.bot.deliveries >= 1
    sim.command('reset')
    assert sim.bot is None and not sim.traffic_running


def test_bot_reserved_next_cell():
    sim = Simulation(position=(1, 7), goal=(3, 7), path=[(1, 7), (2, 7), (3, 7)], status='running')
    from backend.delivery_bot import DeliveryBot
    sim.bot = DeliveryBot((2, 8), path=[(2, 8), (2, 7), (1, 7), (1, 6)])
    sim.move_bot()
    assert sim.bot.position == (2, 8) and sim.bot.status == 'waiting'
