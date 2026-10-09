import React, { useEffect, useState } from 'react';

const actions = { go_to: 'Їхати', load: 'Завантажити', unload: 'Розвантажити' };
const statuses = {
  draft: 'План готовий — перевірте кроки',
  running: 'Виконується',
  paused: 'На паузі',
  blocked: 'Шлях заблоковано — змініть карту й продовжте',
  completed: 'Завершено',
  cancelled: 'Скасовано',
};

export default function MissionPanel({ state, busy, planning, result, onPlan, command }) {
  const [text, setText] = useState(
    'Забери вантаж із приймання, достав на пакування та повернися на базу.',
  );
  const [config, setConfig] = useState(null);
  const [configError, setConfigError] = useState('');
  const stations = Object.fromEntries(
    Object.entries(state?.destinations || {}).map(([id, station]) => [id, station.name]),
  );
  useEffect(() => {
    setText(
      state?.map_id === 'hub'
        ? 'Забери вантаж із приймання №2, достав на пакування №2 та повернися на базу.'
        : 'Забери вантаж із приймання, достав на пакування та повернися на базу.',
    );
  }, [state?.map_id]);
  async function refreshConfig() {
    try {
      const response = await fetch('/api/planner');
      if (!response.ok) throw new Error();
      setConfig(await response.json());
      setConfigError('');
    } catch {
      setConfigError('Не вдалося перевірити підключення моделі.');
    }
  }
  useEffect(() => {
    refreshConfig();
  }, []);
  const mission = state?.mission;
  const active = mission && !['draft', 'completed', 'cancelled'].includes(mission.status);
  const moving =
    ['running', 'waiting', 'servicing'].includes(state?.status) || state?.traffic_running;
  const stats = mission?.stats || result?.stats;
  const incident = state?.recovery;
  const deciding = incident && ['pending', 'requesting'].includes(incident.status);
  const recoveryNames = {
    watching: 'Спостереження за блокуванням',
    pending: 'Агент обирає дію…',
    requesting: 'Агент обирає дію…',
    waiting: 'Агент вирішив почекати',
    operator: 'Потрібна дія оператора',
    resolved: 'Обробку ситуації завершено',
  };
  const decisionNames = {
    wait: 'Почекати',
    retry_route: 'Повторити маршрут',
    ask_operator: 'Запитати оператора',
    detour_base: 'Від’їхати на базу',
  };
  return (
    <section className="panel ai-mission">
      <div className="eyebrow">МІСІЯ З ТЕКСТОВОЇ КОМАНДИ</div>
      <h2>Завдання для R-01</h2>
      <p className="motion-hint">
        {Object.values(stations).join(' · ')}. Один вантаж за раз.
      </p>
      <label htmlFor="mission-command">Що має зробити робот?</label>
      <textarea
        id="mission-command"
        value={text}
        maxLength={1500}
        disabled={busy || active}
        onChange={(event) => setText(event.target.value)}
      />
      <button
        className="primary"
        disabled={
          !state || busy || active || moving || !text.trim() || !config?.configured
        }
        onClick={() => onPlan(text)}
      >
        {planning ? 'Складаємо план…' : 'Скласти план'}
      </button>
      {moving && !active && (
        <p className="motion-hint">Призупиніть рух перед створенням місії.</p>
      )}
      <p className="motion-hint">
        {configError ||
          (config?.configured
            ? `Модель: ${config.model}`
            : 'Додайте API-ключ у .env в корені проєкту.')}
      </p>
      {!config?.configured && (
        <button className="run" onClick={refreshConfig}>
          Перевірити ключ у налаштуваннях
        </button>
      )}
      {result && !mission && (
        <p role="status" className="mission-message">
          {result.proposal.message}
        </p>
      )}
      {mission && (
        <>
          <p className="mission-message">{mission.message}</p>
          <strong role="status">{statuses[mission.status]}</strong>
          {mission.status === 'draft' && (
            <label className="detour-permission">
              <input
                type="checkbox"
                checked={!!mission.allow_base_detour}
                disabled={busy}
                onChange={() => command('recovery_detour')}
              />
              Дозволити агенту тимчасово від’їхати на базу при заторі, а потім повернутися
              до задачі
            </label>
          )}
          {incident && (
            <div className="recovery-panel" role="status">
              <strong>{recoveryNames[incident.status]}</strong>
              <p>{incident.message}</p>
              {incident.status === 'watching' && (
                <p>Без проходу: {incident.blocked_ticks} / 6 тактів</p>
              )}
              {incident.status === 'waiting' && (
                <p>Залишилося очікувати: {incident.wait_left} тактів</p>
              )}
              <small>Звернення агента: {state.recovery_calls} / 3 за місію</small>
              {incident.status === 'operator' && (
                <p>
                  Звільніть прохід і натисніть «Продовжити місію» або скасуйте завдання.
                </p>
              )}
              {deciding && (
                <button className="run" disabled={busy} onClick={() => command('pause')}>
                  Зупинити й вирішити вручну
                </button>
              )}
            </div>
          )}
          {!!state.recovery_log?.length && (
            <details className="recovery-history">
              <summary>Рішення агента ({state.recovery_log.length})</summary>
              {state.recovery_log.map((item, index) => (
                <div key={index}>
                  <strong>
                    {decisionNames[item.action]}
                    {!item.accepted && ' — відхилено перевіркою'}
                  </strong>
                  <p>{item.reason}</p>
                  <small>
                    {(item.stats.latency_ms / 1000).toFixed(2)} с ·{' '}
                    {item.stats.input_tokens ?? '—'} / {item.stats.output_tokens ?? '—'}{' '}
                    токенів
                    {typeof item.stats.cost_usd === 'number' &&
                      ` · $${item.stats.cost_usd.toFixed(6)}`}
                  </small>
                </div>
              ))}
            </details>
          )}
          <ol className="mission-steps">
            {mission.steps.map((step, index) => (
              <li
                key={index}
                className={
                  index < mission.index
                    ? 'done'
                    : index === mission.index && active
                      ? 'current'
                      : ''
                }
              >
                {index < mission.index ? '✓ ' : ''}
                {actions[step.action]} → {stations[step.station]}
                {index === mission.index &&
                  state.status === 'servicing' &&
                  ` (${state.service_left} такти)`}
              </li>
            ))}
          </ol>
          <div className="run-row">
            {!deciding && ['draft', 'paused', 'blocked'].includes(mission.status) && (
              <button
                className="primary"
                disabled={busy}
                onClick={() => command('mission_start')}
              >
                {mission.status === 'draft' ? 'Запустити місію' : 'Продовжити місію'}
              </button>
            )}
            {['running', 'blocked'].includes(mission.status) && (
              <button className="run" disabled={busy} onClick={() => command('pause')}>
                Пауза місії
              </button>
            )}
            {!['completed', 'cancelled'].includes(mission.status) && (
              <button
                className="run"
                disabled={busy}
                onClick={() => command('mission_cancel')}
              >
                Скасувати
              </button>
            )}
          </div>
        </>
      )}
      <div className="metric-row">
        <span>Вантаж R-01</span>
        <b>{state?.cargo ? 'Завантажено' : 'Порожній'}</b>
      </div>
      {stats && (
        <p className="motion-hint">
          Відповідь: {(stats.latency_ms / 1000).toFixed(2)} с · токени:{' '}
          {stats.input_tokens ?? '—'} вхід / {stats.output_tokens ?? '—'} вихід
          {typeof stats.cost_usd === 'number' && ` · $${stats.cost_usd.toFixed(6)}`}
        </p>
      )}
    </section>
  );
}
