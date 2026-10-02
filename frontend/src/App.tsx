import { useEffect, useRef, useState } from 'react';
import { StatTile } from './components/StatTile';
import { Sparkline } from './components/Sparkline';
import { fmtCompact, fmtDuration, fmtGib, fmtInt, fmtMib, fmtMs, fmtNum, fmtPct, fmtRate, fmtTemp, fmtWatts, fmtClock } from './format';
import { effectiveTheme, toggleTheme } from './theme';
import type { Model, Reply, Snapshot } from './types';

function Badge({model}: {model: Model}) {
  const state = model.ready ? 'active' : model.running ? 'loading' : model.status === 'failed' ? 'error' : 'unloaded';
  const label = model.ready ? 'API ready' : model.running ? 'Loading' : !model.installed ? 'Not installed' : model.status === 'failed' ? 'Failed' : 'Stopped';
  return <span className={`badge badge-${state}`}><span className="dot" />{label}</span>;
}

async function json<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`);
  return body;
}

export default function App() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [lastReceivedAt, setLastReceivedAt] = useState(0);
  const [selectedId, setSelectedId] = useState('4b');
  const selectedInitialModel = useRef(false);
  const [connectionError, setConnectionError] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [theme, setTheme] = useState(effectiveTheme());
  const [tab, setTab] = useState<'overview' | 'logs' | 'test'>('overview');
  const [logs, setLogs] = useState<{engine: string; supervisor: string; operation: string} | null>(null);
  const [logError, setLogError] = useState('');
  const [prompt, setPrompt] = useState('Explain what a knowledge graph is in three short sentences.');
  const [maxTokens, setMaxTokens] = useState(128);
  const [reply, setReply] = useState<Reply | null>(null);
  const [testing, setTesting] = useState(false);
  const [confirm, setConfirm] = useState<{action: string; model: Model} | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function update() {
      try {
        const next = await json<Snapshot>('/api/state', {signal: AbortSignal.timeout(15000)});
        if (!stopped) {
          setData(next); setLastReceivedAt(Date.now()); setConnectionError('');
          if (!selectedInitialModel.current) {
            const running = next.models.filter(model => model.running);
            if (running.length === 1) setSelectedId(running[0]!.id);
            selectedInitialModel.current = true;
          }
        }
      } catch (e) {if (!stopped) setConnectionError(String(e));}
      if (!stopped) timer = setTimeout(update, 3000);
    }
    void update();
    return () => {stopped = true; clearTimeout(timer);};
  }, []);

  useEffect(() => {
    setReply(null); setLogs(null); setLogError('');
  }, [selectedId]);

  useEffect(() => {
    if (tab !== 'logs') return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function update() {
      try {
        const next = await json<{engine: string; supervisor: string; operation: string}>(`/api/logs/${selectedId}`, {signal: AbortSignal.timeout(10000)});
        if (!stopped) {setLogs(next); setLogError('');}
      } catch (e) {if (!stopped) setLogError(String(e));}
      if (!stopped) timer = setTimeout(update, 3000);
    }
    void update();
    return () => {stopped = true; clearTimeout(timer);};
  }, [tab, selectedId]);

  async function post<T>(url: string, body: unknown): Promise<T> {
    return json<T>(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  }

  async function operate(action: string, model: Model) {
    setConfirm(null); setError(''); setPending(true);
    try {
      await post('/api/operations', {action, model: model.id});
      setSelectedId(model.id);
      setData(await json<Snapshot>('/api/state')); setLastReceivedAt(Date.now());
    } catch (e) {setError(e instanceof Error ? e.message : String(e));}
    finally {setPending(false);}
  }

  async function tryRequest() {
    setTesting(true); setError(''); setReply(null);
    try {setReply(await post<Reply>('/api/test', {model: selectedId, prompt, max_tokens: maxTokens}));}
    catch (e) {setError(e instanceof Error ? e.message : String(e));}
    finally {setTesting(false);}
  }

  const fresh = !!data && Date.now() - lastReceivedAt < 25000 && !connectionError && !data.error;
  const active = data?.models.filter(m => m.running) ?? [];
  const selected = data?.models.find(m => m.id === selectedId);
  const foreign = data?.host.foreign_processes ?? [];
  const busy = pending || testing || data?.job?.state === 'running';
  const m = selected?.metrics;
  const gpu = data?.host.gpu;
  const ram = data?.host.memory;
  const canDeploy = (model: Model) => {
    const floor = active.some(item => item.id !== model.id) ? 12 : model.min_available_gib;
    return fresh && !busy && foreign.length === 0 && !data?.host.gpu_error &&
      ram?.available_gib != null && ram.available_gib >= floor && ram.swap_used_gib <= 2;
  };
  const gpuSeries = data?.history.filter(h => h.gpu_util != null).map(h => ({ts: h.ts, v: h.gpu_util!})) ?? [];
  const outputSeries = selected?.ready ? data?.history.filter(h => h.models[selectedId]?.session === selected.session && h.models[selectedId]?.output_tps != null)
    .map(h => ({ts: h.ts, v: h.models[selectedId]!.output_tps!})) ?? [] : [];

  return <div className="shell">
    <header className="topbar">
      <a className="brand" href="/" aria-label="Fluxion Spark Console"><img src="/fluxion-icon-transparent.svg" width="26" height="26" alt="" />Fluxion<span className="brand-divider" /><span className="muted">Spark Console</span></a>
      <div className="topbar-right"><span className="host-chip">{data ? `${data.host.user}@${data.host.hostname}` : 'Connecting…'}</span>
        <span className={`live-pill ${fresh ? '' : 'offline'}`}><i />{fresh ? 'Live' : 'Connecting'}</span>
        <button className="theme-toggle" title="Toggle color theme" aria-label="Toggle color theme" onClick={() => setTheme(toggleTheme())}>{theme === 'light' ? '◐' : '☀'}</button></div>
    </header>
    <main className="content console-content">
      {data?.preview && <div className="notice" style={{borderColor: 'var(--accent)', color: 'var(--accent)', background: 'var(--surface)'}} role="status">Design preview · Sample data · No connection to the Spark</div>}
      <div className="page-heading"><div><div className="eyebrow">DGX SPARK · LOCAL INFERENCE</div><h1>Model workspace</h1><p className="page-sub">Deploy, switch, and monitor your models in one place.</p></div><div className="hardware-label">{gpu?.name ?? 'NVIDIA GB10'}<span>Unified memory · Single GPU</span></div></div>
      {(connectionError || data?.error || (data && !fresh)) && <div className="notice danger" role="alert">Connection interrupted. Controls are paused until fresh data arrives. {connectionError || data?.error}</div>}
      {error && <div className="notice danger" role="alert"><span>{error}</span><button className="btn btn-sm" onClick={() => setError('')} aria-label="Dismiss error">Dismiss</button></div>}
      <div className="summary-grid">
        <StatTile label="GPU utilization" value={fmtPct(gpu?.utilization)} sub={`${fmtTemp(gpu?.temperature)} · ${fmtWatts(gpu?.power)}`} />
        <StatTile label="Available memory" value={fmtGib(ram?.available_gib)} sub={`${fmtGib(ram?.total_gib)} unified memory`} />
        <StatTile label="Active model" value={active.length ? active.map(a => a.id.toUpperCase()).join(' + ') : 'None'} sub={active.length ? active.map(a => a.ready ? 'Ready for requests' : 'Loading weights & kernels').join(' · ') : 'Choose a model below'} />
        <StatTile label="Model library" value={<>{data?.models.filter(v => v.installed).length ?? '—'}<span className="value-unit"> / 4</span></>} sub="Profiles detected" />
      </div>

      {foreign.length > 0 && <div className="notice workload" role="status"><span className="notice-icon">ⓘ</span><div><strong>GPU in use by another workload</strong><span>{foreign.map(p => `${p.user} · PID ${p.pid} · ${fmtMib(p.memory_mib)}`).join(' / ')}. Deploy controls unlock when the GPU is available.</span></div></div>}
      {data?.host.gpu_error && <div className="notice danger">GPU availability could not be checked: {data.host.gpu_error}</div>}
      {data?.job && <div className={`job-strip ${data.job.state}`} role="status"><span className={`job-indicator ${data.job.state === 'running' ? 'spin' : ''}`}>{data.job.state === 'succeeded' ? '✓' : data.job.state === 'failed' ? '!' : '◌'}</span><div><strong>{data.job.stage}</strong><span>{data.job.error ?? `${data.models.find(v => v.id === data.job?.model)?.name} · ${fmtDuration((data.job.finished_at ?? Date.now()/1000) - data.job.started_at)}`}</span></div>{data.job.state === 'running' && data.job.action !== 'stop' && <button className="btn btn-sm" onClick={async () => {try {await post('/api/cancel', {});} catch(e) {setError(String(e));}}}>Cancel loading</button>}</div>}

      <section className="library" aria-label="Model library">
        <div className="section-heading"><h2>Models</h2><span className="muted">One model at a time · existing model environments</span></div>
        <div className="model-grid">{data ? data.models.map((model, index) => <article key={model.id} className={`model-card ${selectedId === model.id ? 'selected' : ''}`} style={{'--model-color': `var(--series-${index+1})`} as React.CSSProperties}>
          <button className="model-select" onClick={() => setSelectedId(model.id)} aria-label={`View ${model.name}`} aria-pressed={selectedId === model.id}>
            <div className="model-card-top"><span className="model-size">{model.id.toUpperCase()}</span><Badge model={model} /></div>
            <h3>{model.name}</h3><div className="model-meta"><span>{model.quantization}</span><span>{model.engine}{model.version ? ` ${model.version}` : ''}</span></div>
            <div className="model-bottom-meta"><span>kvcached</span><strong className={model.kvcached ? 'enabled' : ''}>{model.kvcached ? 'Enabled' : 'Off'}</strong></div>
          </button>
          <div className="model-action">{model.running ? <button className="btn btn-stop" disabled={!fresh || !!busy} onClick={() => setConfirm({action: 'stop', model})}>Stop model</button> : <button className={`btn ${selectedId === model.id ? 'btn-primary' : ''}`} disabled={!canDeploy(model) || !model.installed} title={!model.installed ? model.installation_error : !canDeploy(model) ? `Requires ${active.length ? 12 : model.min_available_gib} GiB available memory, low swap use, and an idle GPU` : undefined} onClick={() => active.length ? setConfirm({action: 'switch', model}) : void operate('deploy', model)}>{active.length ? 'Switch to model' : 'Deploy model'}<span aria-hidden>↗</span></button>}</div>
        </article>) : [0,1,2,3].map(i => <div className="model-card skeleton" key={i} />)}</div>
      </section>

      {selected && <section className="detail-panel card">
        <div className="detail-header"><div className="detail-title"><h2>{selected.name}</h2><Badge model={selected} /></div><nav className="detail-tabs" aria-label="Model details">{(['overview','logs','test'] as const).map(t => <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>{t === 'overview' ? 'Overview' : t === 'logs' ? 'Logs' : 'Try API'}</button>)}</nav></div>
        {tab === 'overview' && <div className="overview">
          <div className="endpoint-bar"><span className="label">API endpoint · on Spark</span><code>http://127.0.0.1:{selected.port}/v1</code><button className="btn btn-sm btn-ghost" onClick={async () => {try {await navigator.clipboard.writeText(`http://127.0.0.1:${selected.port}/v1`); setCopied(true); setTimeout(() => setCopied(false), 1500);} catch {setError('Copy unavailable in this browser. Select and copy the endpoint text.');}}}>{copied ? 'Copied' : 'Copy'}</button><span className="endpoint-alias"><b>Model</b> {selected.alias}</span></div>
          {(selected.installation_error || selected.error) && <div className="notice danger model-error" role="alert">{selected.installation_error || selected.error}</div>}
          {!selected.ready && <div className="empty-note">{selected.running ? 'The model is loading. Open Logs to follow startup; larger models can take several minutes.' : 'Deploy this model to see its live request and token metrics.'}</div>}
          {selected.metrics_error && <div className="empty-note">{selected.ready ? 'API is ready; engine metrics are unavailable.' : 'Checking model API health…'} {selected.metrics_error}</div>}
          <div className="engine-stats">
            <StatTile label="Completed requests" value={fmtInt(m?.requests)} sub="Current engine session" />
            <StatTile label="Input tokens" value={fmtCompact(m?.input_tokens)} sub="All clients · current session" />
            <StatTile label="Output tokens" value={fmtCompact(m?.output_tokens)} sub="All clients · current session" />
            <StatTile label="Avg. time to first token" value={fmtMs(m?.ttft_ms)} sub="Engine-reported · session average" />
          </div>
          <div className="charts-grid"><div className="chart-block"><div className="chart-heading"><span>Output throughput</span><strong>{fmtNum(m?.output_tps)} <small>tok/s</small></strong></div><Sparkline data={outputSeries} height={140} color="var(--series-1)" name="Output tok/s" format={v => `${fmtNum(v)} tok/s`} /><p>Token counter change per second across all requests.</p></div><div className="chart-block"><div className="chart-heading"><span>GPU utilization</span><strong>{fmtPct(gpu?.utilization)}</strong></div><Sparkline data={gpuSeries} height={140} color="var(--series-3)" name="GPU utilization" domain={[0,100]} format={v => fmtPct(v)} /><p>Whole GPU, including other workloads · last 10 minutes.</p></div></div>
          <div className="secondary-stats"><span>Running <b>{fmtInt(m?.running)}</b></span><span>Queued <b>{fmtInt(m?.waiting)}</b></span><span>Avg. request latency <b>{fmtMs(m?.latency_ms)}</b></span><span>Draft acceptance <b>{fmtRate(m?.acceptance, 1)}</b></span><span>Model GPU memory <b>{fmtMib(selected.gpu_memory_mib)}</b></span></div>
          <details className="model-details"><summary>Model configuration & metric definitions</summary><div className="detail-definition"><p><b>Speculative decoding:</b> {selected.speculative}. <b>Startup memory floor:</b> {selected.min_available_gib} GiB available. <b>Runtime:</b> <code>{selected.runtime}</code></p><p>Totals reset when the model service restarts. Throughput measures output tokens across requests over the sampling interval; it is not an isolated decode benchmark. Unavailable metrics appear as —. TTFT and latency are engine-reported averages.</p><p>The Spark uses unified memory. Available system memory and per-process GPU allocations describe different views of shared memory; do not add them together. Sampling happens every 3 seconds.</p><p><b>Access from your laptop:</b> forward this model's port over SSH. The console tunnel alone forwards only the web interface.</p></div></details>
        </div>}
        {tab === 'logs' && <div className="logs-panel"><div className="log-heading"><span>Latest engine output</span><span>Refreshes every 3 seconds · includes previous session until next launch</span></div>{logError && <p role="alert">{logError}</p>}<pre className="engine-log">{logs ? [logs.operation, logs.engine].filter(Boolean).join('\n\n') || 'No engine log yet. Deploy this model to begin.' : 'Loading logs…'}</pre><details><summary>Supervisor log</summary><pre className="engine-log small">{logs?.supervisor || 'No supervisor log.'}</pre></details></div>}
        {tab === 'test' && <div className="test-panel"><div className="test-intro"><h3>Send a test request</h3><p>A short request to the selected model. Its tokens appear in the session totals.</p></div><label className="prompt-label" htmlFor="prompt">Prompt</label><textarea id="prompt" rows={4} maxLength={8000} value={prompt} onChange={e => setPrompt(e.target.value)} disabled={testing} /><div className="test-controls"><label>Max output tokens <select value={maxTokens} onChange={e => setMaxTokens(Number(e.target.value))}>{[64,128,256,512].map(n => <option key={n}>{n}</option>)}</select></label><span className="muted">{!selected.ready ? 'Deploy this model first.' : 'Temperature 0'}</span><button className="btn btn-primary" disabled={!fresh || !selected.ready || !!busy || !prompt.trim()} onClick={() => void tryRequest()}>{testing ? 'Generating…' : 'Send request ↗'}</button></div>{reply && <div className="response"><div className="response-heading"><strong>Response</strong><span>{fmtNum(reply.elapsed_s)} s · {fmtInt(reply.usage.prompt_tokens)} input · {fmtInt(reply.usage.completion_tokens)} output tokens · {fmtNum(reply.request_output_tps)} output tok/s (request E2E)</span></div><div className="response-text">{reply.content || '(No text returned)'}</div>{reply.finish_reason === 'length' && <p className="muted">Reached the selected output token limit.</p>}</div>}</div>}
      </section>}

      <section className="activity-section"><div className="section-heading"><h2>Recent activity</h2><span className="muted">Model operations & test requests</span></div><div className="activity-list">{data?.events.length ? data.events.slice(0,8).map(event => <div className="activity-row" key={event.id}><span className={`event-dot ${event.level}`} /><span className="event-message">{event.message}</span><time>{fmtClock(event.ts)}</time></div>) : <p className="muted">Activity will appear here.</p>}</div></section>
      <footer className="console-footer"><span>Fluxion · DGX Spark</span><span>{data?.ts ? `Last sample ${fmtClock(data.ts)}` : 'Waiting for telemetry'}</span></footer>
    </main>
    {confirm && <div className="confirm-backdrop" onClick={() => setConfirm(null)} onKeyDown={event => {if (event.key === 'Escape') setConfirm(null);}}><div className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title" onClick={e => e.stopPropagation()}><h2 id="confirm-title">{confirm.action === 'stop' ? `Stop ${confirm.model.name}?` : `Switch to ${confirm.model.name}?`}</h2><p>{confirm.action === 'stop' ? 'This stops the model API and any unfinished requests. You can deploy it again at any time.' : `The current model will stop before ${confirm.model.name} loads. A request that arrives after the idle check can be interrupted, so switch during a maintenance window. The API will be unavailable while the new model loads.`}</p><div className="confirm-actions"><button className="btn" autoFocus onClick={() => setConfirm(null)}>Cancel</button><button className="btn btn-primary" onClick={() => void operate(confirm.action, confirm.model)}>{confirm.action === 'stop' ? 'Stop model' : 'Switch model'}</button></div></div></div>}
  </div>;
}
