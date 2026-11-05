import React, { useEffect, useState } from 'react';

const modes = { astar: 'A* і очікування', rules: 'A* і правила', agent: 'A* і LLM' };
const outcomes = { completed: 'Виконано', timeout: 'Ліміт тактів', operator_required: 'Потрібен оператор', provider_error: 'Помилка API' };

export default function ExperimentPanel() {
  const [catalog, setCatalog] = useState(null);
  const [scenario, setScenario] = useState('long');
  const [variant, setVariant] = useState(0);
  const [agent, setAgent] = useState(false);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [tick, setTick] = useState(0);
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    fetch('/api/experiments').then(r => {
      if (!r.ok) throw new Error('Не вдалося завантажити сценарії.');
      return r.json();
    }).then(setCatalog).catch(e => setError(e.message));
  }, []);
  useEffect(() => {
    if (!job || !busy) return;
    let stopped = false, timer;
    async function poll() {
      try {
        const r = await fetch(`/api/experiments/${job}`);
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || 'Не вдалося прочитати результат.');
        if (stopped) return;
        setResult(data);
        if (data.status !== 'running') {
          setBusy(false);
          if (data.error) setError(data.error);
        } else timer = setTimeout(poll, 1000);
      } catch (e) {
        if (!stopped) { setError(e.message); setBusy(false); }
      }
    }
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [job, busy]);
  const trials = result?.trials || [];
  const maxTick = Math.max(0, ...trials.map(t => t.metrics.elapsed_ticks));
  useEffect(() => {
    if (!playing) return;
    if (tick >= maxTick) { setPlaying(false); return; }
    const timer = setTimeout(() => setTick(n => n + 1), 130);
    return () => clearTimeout(timer);
  }, [playing, tick, maxTick]);
  async function run() {
    setBusy(true); setError(''); setResult(null); setJob(null); setTick(0); setPlaying(false);
    try {
      const r = await fetch('/api/experiments', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario, variant, include_agent: agent }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Помилка запуску.');
      setJob(data.id);
    } catch (e) { setError(e.message); setBusy(false); }
  }
  return <details className="experiment-panel" open>
    <summary>Експерименти · порівняти стратегії</summary>
    <p>Однакова доставка й перешкоди для всіх режимів. Обчислення виконує сервер; нижче можна переграти запис руху.</p>
    <div className="experiment-controls">
      <label>Сценарій<select value={scenario} disabled={busy} onChange={e => setScenario(e.target.value)}>
        {Object.entries(catalog?.scenarios || {}).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select></label>
      <label>Варіант<select value={variant} disabled={busy} onChange={e => setVariant(Number(e.target.value))}>
        {[0, 1, 2, 3, 4].map(n => <option key={n} value={n}>{n + 1}</option>)}
      </select></label>
      <label><input type="checkbox" checked={agent} disabled={busy || !catalog?.agent_configured}
        onChange={e => setAgent(e.target.checked)} /> Додати LLM (витрачає API-бюджет)</label>
      <button className="primary" disabled={busy || !catalog} onClick={run}>{busy ? 'Триває вимірювання…' : 'Запустити порівняння'}</button>
    </div>
    {!catalog?.agent_configured && <p>Без ключа доступні два режими: A* та фіксовані правила.</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {trials.length > 0 && <>
      <div className="experiment-table"><table><thead><tr>
        {['Режим', 'Результат', 'Такт завершення', 'Шлях', 'Простій', 'Запити оператора', 'LLM, с'].map(x => <th key={x}>{x}</th>)}
      </tr></thead><tbody>{trials.map(({ metrics: m }) => <tr key={m.mode}>
        <td>{modes[m.mode]}</td><td>{outcomes[m.outcome]}</td><td>{m.completion_ticks ?? '—'}</td>
        <td>{m.path_steps}</td><td>{m.waiting_ticks}</td><td>{m.operator_requests}</td><td>{(m.llm_latency_ms / 1000).toFixed(2)}</td>
      </tr>)}</tbody></table></div>
      <p>Незавершені місії не мають часу завершення. Простій і шлях виміряно до зупинки запуску. Час відповіді LLM наведено окремо від тактів.</p>
      <div className="experiment-controls">
        <button className="run" onClick={() => { if (tick >= maxTick) setTick(0); setPlaying(p => !p); }}>{playing ? 'Пауза запису' : 'Відтворити рух'}</button>
        <label>Такт {tick} / {maxTick}<input aria-label="Такт запису" type="range" min="0" max={maxTick} value={tick} onChange={e => { setTick(Number(e.target.value)); setPlaying(false); }} /></label>
        <a href={`/api/experiments/${job}/csv`} download>Завантажити CSV</a>
      </div>
      <div className="experiment-replays">{trials.map(trial => {
        const frame = trial.trace[Math.min(tick, trial.trace.length - 1)];
        const pts = frame.path.map(([x, y]) => `${x + 0.5},${y + 0.5}`).join(' ');
        return <article key={trial.metrics.mode}><h3>{modes[trial.metrics.mode]}</h3>
          <svg viewBox="0 0 24 16" role="img" aria-label={`Запис руху ${modes[trial.metrics.mode]}`}>
            <rect width="24" height="16" fill="#f0f5f2" />
            {trial.map.shelves.map(([x, y, w, h], i) => <rect key={i} x={x} y={y} width={w} height={h} fill="#bbcfc5" />)}
            {trial.map.barrier.map(([x, y]) => <rect key={`${x},${y}`} x={x} y={y} width="1" height="1" fill="#68796f" />)}
            {frame.gate_active && <rect x="3" y="7" width="1" height="1" fill="#c66d35" />}
            {Object.entries(trial.map.destinations).map(([id, s]) => <circle key={id} cx={s.position[0]+0.5} cy={s.position[1]+0.5} r="0.35" fill="#c5a659" />)}
            <polyline points={pts} fill="none" stroke="#4c9779" strokeWidth="0.16" />
            <circle cx={frame.position[0]+0.5} cy={frame.position[1]+0.5} r="0.44" fill="#173e31" />
          </svg>
          <p>Такт {frame.tick} · пройдено {frame.steps} · простій {frame.waiting_ticks} · {frame.cargo ? 'з вантажем' : 'без вантажу'}</p>
          <p>{frame.message}</p>
          {trial.metrics.error && <p role="alert">{trial.metrics.error}</p>}
          {trial.requests.filter(r => r.decision).map((r, i) => <p key={i}>LLM, такт {r.tick}: {r.decision.reason}</p>)}
        </article>;
      })}</div>
    </>}
  </details>;
}
