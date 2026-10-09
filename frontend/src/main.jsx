import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Box,
  Route,
  Play,
  Pause,
  RotateCcw,
  MousePointer2,
  Square,
  ArrowUpRight,
  Radio,
  Flag,
  ChevronRight,
  Check,
  Bot,
  Activity,
} from 'lucide-react';
import './style.css';
import MissionPanel from './MissionPanel';

const names = {
  idle: 'Очікує ціль',
  ready: 'Маршрут готовий',
  running: 'Виконує маршрут',
  paused: 'На паузі',
  arrived: 'Цілі досягнуто',
  blocked: 'Шлях заблоковано',
  waiting: 'Очікує звільнення проходу',
  servicing: 'Операція з вантажем',
};
const same = (a, b) => a && b && a[0] === b[0] && a[1] === b[1];

function App() {
  const [state, setState] = useState(null),
    [session, setSession] = useState(null);
  const [mode, setMode] = useState('goal'),
    [speed, setSpeed] = useState(2);
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [planning, setPlanning] = useState(false);
  const [proposalResult, setProposalResult] = useState(null);
  const [autoStopped, setAutoStopped] = useState(false);
  const recoveryTickets = useRef(new Set());
  const activeSession = useRef(null);
  const [maps, setMaps] = useState([]);
  const [zoom, setZoom] = useState(1);
  function acceptState(next) {
    setState((previous) =>
      !previous || next.revision >= previous.revision ? next : previous,
    );
  }
  useEffect(() => {
    const incident = state?.recovery;
    if (
      !session ||
      incident?.status !== 'pending' ||
      recoveryTickets.current.has(incident.ticket)
    )
      return;
    recoveryTickets.current.add(incident.ticket);
    async function recover() {
      try {
        const response = await fetch(`/api/sessions/${session}/recovery`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ticket: incident.ticket }),
        });
        const data = await response.json();
        if (!response.ok)
          throw new Error(
            typeof data.detail === 'string'
              ? data.detail
              : 'Не вдалося отримати рішення агента.',
          );
        if (activeSession.current === session) acceptState(data);
      } catch (error) {
        if (activeSession.current !== session) return;
        // No automatic retry: a timed-out provider request might still have been billed.
        await command('pause');
        setError(
          error.message +
            ' Місія залишається на паузі. Можна скасувати її або продовжити вручну.',
        );
      }
    }
    recover();
  }, [session, state?.recovery?.ticket, state?.recovery?.status]);
  async function planMission(text) {
    if (pending.current || !session) return;
    pending.current = true;
    setBusy(true);
    setPlanning(true);
    setError('');
    setProposalResult(null);
    try {
      const response = await fetch(`/api/sessions/${session}/mission`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const data = await response.json();
      if (!response.ok)
        throw new Error(
          typeof data.detail === 'string' ? data.detail : 'Не вдалося скласти місію.',
        );
      setState(data.state);
      setProposalResult(data);
    } catch (error) {
      setError(error.message);
    } finally {
      pending.current = false;
      setBusy(false);
      setPlanning(false);
    }
  }
  async function connect(mapId = 'north') {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError('');
    try {
      if (activeSession.current) {
        const paused = await fetch(`/api/sessions/${activeSession.current}/command`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'pause' }),
        });
        if (!paused.ok) throw new Error('Не вдалося призупинити попередню симуляцію.');
        acceptState(await paused.json());
      }
      const r = await fetch(`/api/sessions?map_id=${encodeURIComponent(mapId)}`, {
        method: 'POST',
      });
      if (!r.ok) throw new Error('Не вдалося відкрити сесію.');
      const data = await r.json();
      activeSession.current = data.id;
      setSession(data.id);
      setState(data.state);
      setProposalResult(null);
      setAutoStopped(false);
      setZoom(1);
      setMode('goal');
      recoveryTickets.current.clear();
    } catch (e) {
      setError('Сервер недоступний. Перевірте запуск Python і повторіть.');
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  useEffect(() => {
    connect();
    fetch('/api/maps')
      .then((response) => {
        if (!response.ok) throw new Error();
        return response.json();
      })
      .then(setMaps)
      .catch(() => setError('Не вдалося завантажити перелік карт. Оновіть сторінку.'));
  }, []);
  async function command(action, position) {
    if (pending.current || !session) return;
    pending.current = true;
    setBusy(true);
    setError('');
    try {
      const r = await fetch(`/api/sessions/${session}/command`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, position }),
      });
      const data = await r.json();
      if (!r.ok)
        throw new Error(
          typeof data.detail === 'string' ? data.detail : 'Некоректна команда.',
        );
      acceptState(data);
      setAutoStopped(false);
      if (action === 'reset') setProposalResult(null);
    } catch (e) {
      setError(e.message);
      // Keep server-owned state intact; stop polling on a failed tick.
      if (action === 'step') setAutoStopped(true);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  useEffect(() => {
    if (
      busy ||
      autoStopped ||
      (!['running', 'waiting', 'servicing'].includes(state?.status) &&
        !state?.traffic_running &&
        !state?.recovery_needs_tick)
    )
      return;
    const timer = setTimeout(() => command('step'), 1000 / speed);
    return () => clearTimeout(timer);
  }, [state, speed, busy, autoStopped]);

  const running = ['running', 'waiting', 'servicing'].includes(state?.status);
  const missionLocked =
    state?.mission && !['completed', 'cancelled'].includes(state.mission.status);
  const remaining = Math.max(0, (state?.path.length || 1) - 1);
  const progress = state?.mission
    ? Math.round((state.mission.index / state.mission.steps.length) * 100)
    : state
      ? state.status === 'arrived'
        ? 100
        : state.path.length
          ? Math.round((state.steps / Math.max(1, state.steps + remaining)) * 100)
          : 0
      : 0;
  const pts = (arr) => arr.map(([x, y]) => `${x * 32 + 16},${y * 32 + 16}`).join(' ');
  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand-icon">
          <Route size={23} />
        </div>
        <div className="rail-line" />
        <div className="rail-active" title="Симуляція">
          <Box size={22} />
        </div>
        <span className="rail-bottom">RP</span>
      </aside>
      <div className="workspace">
        <header>
          <div className="brand">
            RoutePlanning <span>/</span> <small>Лабораторія місій</small>
          </div>
          <div className="local">
            <span />
            Локальна симуляція
          </div>
        </header>
        <main>
          <div className="heading">
            <div>
              <div className="eyebrow">РОБОЧЕ СЕРЕДОВИЩЕ / 01</div>
              <h1>Склад. Маршрут. Рух.</h1>
              <p>Плануйте шлях і спостерігайте, як платформа виконує завдання.</p>
            </div>
            <div className="version">
              ПРОТОТИП <b>v0.6</b>
            </div>
          </div>
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          <div className="map-selection">
            <label htmlFor="warehouse-map">Карта складу</label>
            <select
              id="warehouse-map"
              value={state?.map_id || 'north'}
              disabled={busy || planning || !state}
              onChange={(event) => connect(event.target.value)}
            >
              {maps.map((map) => (
                <option key={map.id} value={map.id}>
                  {map.name} — {map.width} × {map.height}
                </option>
              ))}
            </select>
            <span>Зміна карти відкриває нову симуляцію.</span>
          </div>
          <div className="layout">
            <section className="map-card">
              <div className="card-top">
                <div>
                  <h2>
                    <Box size={18} /> {state?.map_name || 'Склад'}
                  </h2>
                  <span>
                    {state?.width || 24} × {state?.height || 16} клітинок <i>·</i> Вид
                    зверху
                  </span>
                </div>
                <div className={'status ' + (state?.status || 'idle')}>
                  <span />
                  {state ? names[state.status] : 'Завантаження…'}
                </div>
              </div>
              <div className="toolbar">
                <div className="segmented">
                  <button
                    className={mode === 'goal' ? 'selected' : ''}
                    onClick={() => setMode('goal')}
                  >
                    <MousePointer2 size={15} /> Вибрати ціль
                  </button>
                  <button
                    className={mode === 'obstacle' ? 'selected' : ''}
                    onClick={() => setMode('obstacle')}
                  >
                    <Square size={14} /> Перешкода
                  </button>
                  <button
                    className={mode === 'moving_horizontal' ? 'selected' : ''}
                    onClick={() => setMode('moving_horizontal')}
                  >
                    Візок ↔
                  </button>
                  <button
                    className={mode === 'moving_vertical' ? 'selected' : ''}
                    onClick={() => setMode('moving_vertical')}
                  >
                    Візок ↕
                  </button>
                </div>
                <span>
                  {mode === 'goal'
                    ? 'Клікніть на вільну клітинку'
                    : mode === 'obstacle'
                      ? 'Клік — додати або прибрати палету'
                      : 'Клік — додати візок. Клік по візку — прибрати. Рух після запуску.'}
                </span>
              </div>
              <div className="map-zoom">
                <span>{state?.map_description}</span>
                <label>
                  Масштаб{' '}
                  <select
                    aria-label="Масштаб карти"
                    value={zoom}
                    onChange={(event) => setZoom(Number(event.target.value))}
                  >
                    <option value="1">Уся карта</option>
                    <option value="1.5">150%</option>
                    <option value="2">200%</option>
                  </select>
                </label>
              </div>
              <div className="map-wrap">
                {state ? (
                  <svg
                    className="map"
                    viewBox={`-26 -26 ${state.width * 32 + 52} ${state.height * 32 + 52}`}
                    style={{
                      width: `${zoom * 100}%`,
                      maxWidth: 'none',
                      maxHeight: zoom === 1 ? '590px' : 'none',
                    }}
                    role="group"
                    aria-label="Карта складу. Виберіть клітинку або станцію."
                  >
                    <defs>
                      <pattern
                        id="grid"
                        width="32"
                        height="32"
                        patternUnits="userSpaceOnUse"
                      >
                        <path
                          d="M 32 0 L 0 0 0 32"
                          fill="none"
                          stroke="#dce4e3"
                          strokeWidth="1"
                        />
                      </pattern>
                    </defs>
                    <rect
                      x="0"
                      y="0"
                      width={state.width * 32}
                      height={state.height * 32}
                      rx="4"
                      fill="#f6f8f5"
                    />
                    <rect
                      width={state.width * 32}
                      height={state.height * 32}
                      fill="url(#grid)"
                    />
                    {Array.from({ length: state.width }, (_, i) => (
                      <text
                        key={'x' + i}
                        x={i * 32 + 16}
                        y="-10"
                        textAnchor="middle"
                        className="coord"
                      >
                        {i}
                      </text>
                    ))}
                    {Array.from({ length: state.height }, (_, i) => (
                      <text
                        key={'y' + i}
                        x="-13"
                        y={i * 32 + 20}
                        textAnchor="middle"
                        className="coord"
                      >
                        {i}
                      </text>
                    ))}
                    <rect
                      x={(state.base[0] - 1) * 32}
                      y={(state.base[1] - 1) * 32}
                      width="96"
                      height="96"
                      fill="#e2eee9"
                    />
                    <text
                      x={state.base[0] * 32 + 16}
                      y={(state.base[1] - 1) * 32 + 22}
                      textAnchor="middle"
                      className="zone-label"
                    >
                      БАЗА
                    </text>
                    {state.stations.map((s, i) => (
                      <g key={s.name}>
                        <rect
                          x={s.position[0] * 32}
                          y={s.position[1] * 32}
                          width="32"
                          height="32"
                          rx="5"
                          fill="#eadfc8"
                        />
                        <text
                          x={s.position[0] * 32 + 16}
                          y={s.position[1] * 32 + 21}
                          textAnchor="middle"
                          fill="#8a6b32"
                          fontSize="13"
                          fontWeight="700"
                        >
                          {i + 1}
                        </text>
                      </g>
                    ))}
                    {state.shelves.map(([x, y, w, h], i) => (
                      <g key={i}>
                        <rect
                          x={x * 32 + 3}
                          y={y * 32 + 3}
                          width={w * 32 - 6}
                          height={h * 32 - 6}
                          rx="5"
                          fill="#d2dcd9"
                          stroke="#afc0ba"
                        />
                        {[1, 2, 3].map((n) => (
                          <path
                            key={n}
                            d={`M${x * 32 + 10} ${y * 32 + n * 32}h${w * 32 - 20}`}
                            stroke="#afc0ba"
                          />
                        ))}
                        <rect
                          x={x * 32 + 23}
                          y={y * 32 + 51}
                          width="50"
                          height="26"
                          rx="4"
                          fill="#edf2ef"
                        />
                        <text
                          x={x * 32 + 48}
                          y={y * 32 + 69}
                          textAnchor="middle"
                          className="rack-label"
                        >
                          A-{String(i + 1).padStart(2, '0')}
                        </text>
                      </g>
                    ))}
                    {state.trail.length > 1 && (
                      <polyline
                        points={pts(state.trail)}
                        fill="none"
                        stroke="#bdc9c5"
                        strokeWidth="4"
                        strokeDasharray="3 6"
                        strokeLinecap="round"
                      />
                    )}
                    {state.path.length > 1 && (
                      <polyline
                        points={pts(state.path)}
                        fill="none"
                        stroke="#188477"
                        strokeWidth="5"
                        strokeLinejoin="round"
                        strokeLinecap="round"
                      />
                    )}
                    {state.obstacles.map(([x, y]) => (
                      <g key={`${x},${y}`}>
                        <rect
                          x={x * 32 + 4}
                          y={y * 32 + 4}
                          width="24"
                          height="24"
                          rx="3"
                          fill="#d89351"
                          stroke="#b66d30"
                        />
                        <path
                          d={`M${x * 32 + 10} ${y * 32 + 5}v22m12-22v22`}
                          stroke="#edbd86"
                          strokeWidth="2"
                        />
                      </g>
                    ))}
                    {(state.movers || []).map((m, i) => (
                      <g
                        key={'mover-' + i}
                        transform={`translate(${m.position[0] * 32 + 16},${m.position[1] * 32 + 16})`}
                      >
                        <rect
                          x="-13"
                          y="-12"
                          width="26"
                          height="24"
                          rx="5"
                          fill="#6251b5"
                          stroke="#44348b"
                          strokeWidth="2"
                        />
                        <text y="5" textAnchor="middle" fill="white" fontSize="18">
                          {m.direction[0]
                            ? m.direction[0] > 0
                              ? '→'
                              : '←'
                            : m.direction[1] > 0
                              ? '↓'
                              : '↑'}
                        </text>
                      </g>
                    ))}
                    {state.bot && (
                      <g>
                        {state.bot.path.length > 1 && (
                          <polyline
                            points={pts(state.bot.path)}
                            fill="none"
                            stroke="#287bc0"
                            strokeWidth="3"
                            strokeDasharray="5 5"
                          />
                        )}
                        <g
                          transform={`translate(${state.bot.position[0] * 32 + 16},${state.bot.position[1] * 32 + 16})`}
                        >
                          <rect
                            x="-12"
                            y="-12"
                            width="24"
                            height="24"
                            rx="5"
                            fill="#287bc0"
                          />
                          <text y="5" textAnchor="middle" fill="white" fontSize="13">
                            B
                          </text>
                        </g>
                      </g>
                    )}
                    {state.goal && (
                      <g
                        transform={`translate(${state.goal[0] * 32 + 16},${state.goal[1] * 32 + 16})`}
                      >
                        <circle r="12" fill="#f6f8f5" stroke="#188477" strokeWidth="2" />
                        <circle r="5" fill="#188477" />
                      </g>
                    )}
                    <g
                      transform={`translate(${state.position[0] * 32 + 16},${state.position[1] * 32 + 16})`}
                    >
                      <circle r="21" fill="#188477" opacity=".12" />
                      <rect
                        x="-12"
                        y="-11"
                        width="24"
                        height="22"
                        rx="7"
                        fill="#173f3c"
                      />
                      <rect x="-6" y="-5" width="12" height="7" rx="2" fill="#a6e5cd" />
                      <circle cx="-5" cy="7" r="2" fill="#fff" />
                      <circle cx="5" cy="7" r="2" fill="#fff" />
                    </g>
                    {Array.from({ length: state.height }, (_, y) =>
                      Array.from({ length: state.width }, (_, x) => {
                        const blocked = state.shelves.some(
                          ([sx, sy, w, h]) =>
                            x >= sx && x < sx + w && y >= sy && y < sy + h,
                        );
                        return (
                          !blocked && (
                            <rect
                              key={`${x}-${y}`}
                              className="hit-cell"
                              x={x * 32}
                              y={y * 32}
                              width="32"
                              height="32"
                              fill="transparent"
                              role="button"
                              tabIndex="0"
                              aria-label={`Клітинка ${x}, ${y}`}
                              onClick={() => command(mode, [x, y])}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault();
                                  command(mode, [x, y]);
                                }
                              }}
                            />
                          )
                        );
                      }),
                    )}
                  </svg>
                ) : (
                  <div className="loading">
                    {error ? 'Немає з’єднання із сервером' : 'Завантаження складу…'}
                    {error && (
                      <button onClick={() => connect()}>Повторити підключення</button>
                    )}
                  </div>
                )}
              </div>
              <div className="legend">
                <span>
                  <i className="dot robot" />
                  Платформа
                </span>
                <span>
                  <i className="line" />
                  Маршрут
                </span>
                <span>
                  <i className="dot shelf" />
                  Стелаж
                </span>
                <span>
                  <i className="dot obstacle" />
                  Палета
                </span>
                <span>
                  <i className="dot mover" />
                  Рухомий візок
                </span>
                <span className="scale">1 клітинка = 1 умовний метр</span>
              </div>
            </section>
            <aside className="control-column">
              <MissionPanel
                state={state}
                busy={busy}
                planning={planning}
                result={proposalResult}
                onPlan={planMission}
                command={command}
              />
              <section className="panel">
                <div className="eyebrow">АВТОНОМНИЙ БОТ ДОСТАВКИ</div>
                <p className="motion-hint">
                  Патруль робочих станцій цієї карти. Зупинка на станції — 3 такти. R-01
                  має пріоритет наступної клітинки.
                </p>
                <button
                  className="primary"
                  disabled={!state || busy}
                  onClick={() => command('bot')}
                >
                  <Bot size={17} />{' '}
                  {state?.bot ? 'Прибрати бота' : 'Додати бота доставки'}
                </button>
                {state?.bot && (
                  <>
                    <div className="metric-row">
                      <span>Стан</span>
                      <b>
                        {
                          {
                            moving: 'Рухається',
                            waiting: 'Чекає прохід',
                            servicing: 'Обслуговування',
                          }[state.bot.status]
                        }
                      </b>
                    </div>
                    <div className="metric-row">
                      <span>Ціль</span>
                      <b>{state.bot.target.name}</b>
                    </div>
                    <div className="metric-row">
                      <span>Обслужено станцій</span>
                      <b>{state.bot.deliveries}</b>
                    </div>
                    <div className="metric-row">
                      <span>Очікування / обслуговування</span>
                      <b>
                        {state.bot.status === 'servicing'
                          ? state.bot.service_left
                          : state.bot.waiting_ticks}{' '}
                        тактів
                      </b>
                    </div>
                    <button
                      className="run"
                      disabled={busy || running}
                      onClick={() => command('traffic')}
                    >
                      {state.traffic_running
                        ? 'Зупинити середовище'
                        : 'Запустити без місії R-01'}
                    </button>
                    <p className="motion-hint">
                      Бот рухається разом із місією R-01 або окремо цією кнопкою.
                      Швидкість задається нижче. Зміна палет може спричинити обхід.
                    </p>
                  </>
                )}
              </section>
              <section className="panel mission">
                <div className="eyebrow">ПОТОЧНЕ ЗАВДАННЯ</div>
                <h2>
                  Дістатися цілі <ArrowUpRight size={20} />
                </h2>
                <div className="route-summary">
                  <div>
                    <span className="point start" />
                    <span>Платформа R-01</span>
                    <b>{state ? `${state.position[0]}, ${state.position[1]}` : '—'}</b>
                  </div>
                  <div>
                    <span className="point end" />
                    <span>Точка призначення</span>
                    <b>
                      {state?.goal ? `${state.goal[0]}, ${state.goal[1]}` : 'Не вибрано'}
                    </b>
                  </div>
                </div>
                <div className="station-label">АБО ВИБЕРІТЬ СТАНЦІЮ</div>
                <div className="stations">
                  {state?.stations.map((s, i) => (
                    <button
                      key={s.name}
                      disabled={busy || missionLocked}
                      onClick={() => {
                        setMode('goal');
                        command('goal', s.position);
                      }}
                    >
                      <span>{i + 1}</span>
                      {s.name}
                      <ChevronRight size={14} />
                    </button>
                  ))}
                </div>
                <button
                  className="primary"
                  disabled={!state?.goal || busy || running || missionLocked}
                  onClick={() => command('plan')}
                >
                  <Route size={17} /> Побудувати маршрут
                </button>
                <div className="run-row">
                  <button
                    className="run"
                    disabled={
                      busy ||
                      missionLocked ||
                      (!['ready', 'paused', 'running', 'waiting'].includes(
                        state?.status,
                      ) &&
                        !(
                          state?.status === 'blocked' &&
                          (state?.movers?.length || state?.bot)
                        ))
                    }
                    onClick={() => command(running ? 'pause' : 'run')}
                  >
                    {running ? <Pause size={16} /> : <Play size={16} />}{' '}
                    {running
                      ? 'Пауза'
                      : state?.status === 'paused'
                        ? 'Продовжити'
                        : 'Запустити'}
                  </button>
                  <button
                    className="reset"
                    title="Скинути симуляцію"
                    aria-label="Скинути симуляцію"
                    disabled={!state || busy}
                    onClick={() => command('reset')}
                  >
                    <RotateCcw size={17} />
                  </button>
                </div>
                <p className="motion-hint">
                  Візки рухаються на один крок за такт, розвертаються біля стін і палет.
                  Пауза зупиняє всю симуляцію.
                </p>
                <div className="speed">
                  <span>Швидкість симуляції</span>
                  <select
                    aria-label="Швидкість симуляції"
                    value={speed}
                    onChange={(e) => setSpeed(Number(e.target.value))}
                  >
                    <option value="1">1 крок/с</option>
                    <option value="2">2 кроки/с</option>
                    <option value="4">4 кроки/с</option>
                  </select>
                </div>
              </section>
              <section className="panel metrics">
                <div className="eyebrow">
                  {state?.mission ? 'ВИКОНАННЯ МІСІЇ' : 'ВИКОНАННЯ МАРШРУТУ'}
                </div>
                <div className="progress-head">
                  <strong>
                    {progress}
                    <small>%</small>
                  </strong>
                  <span>
                    {state?.status === 'arrived' ? (
                      <Check size={18} />
                    ) : (
                      <Activity size={18} />
                    )}
                  </span>
                </div>
                <div className="progress">
                  <div style={{ width: progress + '%' }} />
                </div>
                <div className="metric-row">
                  <span>Пройдено</span>
                  <b>{state?.steps || 0} кроків</b>
                </div>
                <div className="metric-row">
                  <span>{state?.mission ? 'Залишилося на етапі' : 'Залишилося'}</span>
                  <b>{state?.path.length ? remaining + ' кроків' : '—'}</b>
                </div>
                <div className="metric-row">
                  <span>Час планування</span>
                  <b>{state?.plans ? state.planning_ms + ' мс' : '—'}</b>
                </div>
                <div className="metric-row">
                  <span>Побудов маршруту</span>
                  <b>{state?.plans || 0}</b>
                </div>
              </section>
            </aside>
          </div>
          <section className="events">
            <div className="events-title">
              <h2>
                <Radio size={18} /> Журнал подій
              </h2>
              <span>Рішення та зміни під час виконання</span>
            </div>
            <div className="event-list" aria-live="polite">
              {state?.events.slice(0, 6).map((e) => (
                <div className="event" key={e.id}>
                  <span className="event-number">{String(e.id).padStart(2, '0')}</span>
                  <span className="event-dot" />
                  <p>{e.message}</p>
                </div>
              ))}
            </div>
          </section>
          <footer>
            <span>
              RoutePlanning <b>·</b> Підтримка виконання місій мобільної платформи
            </span>
            <span>2D-модель · рух у 4 напрямках</span>
          </footer>
        </main>
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
