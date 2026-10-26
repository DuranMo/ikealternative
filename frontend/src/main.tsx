import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { SceneEditor, type SceneLight, type ScenePreview } from './SceneEditor';
import { ColorControls } from './ColorControls';

type Light = SceneLight & {room_id: string; room: string; is_on: boolean; brightness: number | null; reachable: boolean | null};
type Scene = {id: string; name: string; kind: string; daily_time: string | null; next_run: string | null};
async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {method, headers: body ? {'Content-Type': 'application/json'} : undefined, body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(20000)});
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(typeof data?.detail === 'string' ? data.detail : `Request failed (${response.status}). Check your backend and try again.`);
  }
  return response.status === 204 ? undefined as T : response.json();
}
function Slider({label, value, disabled, commit}: {label: string; value: number; disabled: boolean; commit: (n: number) => void}) {
  const [draft, setDraft] = useState(value);
  const editing = useRef(false);
  const latest = useRef(value);
  useEffect(() => { if (!editing.current) { setDraft(value); latest.current = value; } }, [value]);
  function finish() {
    if (editing.current) { editing.current = false; setDraft(value); commit(latest.current); }
  }
  return <label className="slider"><span>{label}</span><input type="range" min="1" max="100" value={draft} disabled={disabled}
    onChange={e => { editing.current = true; latest.current = Number(e.target.value); setDraft(latest.current); }}
    onPointerUp={finish} onPointerCancel={() => { editing.current = false; setDraft(value); }}
    onKeyUp={e => { if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'].includes(e.key)) finish(); }}
    onBlur={finish}/><output>{draft}%</output></label>;
}
function App() {
  const [lights, setLights] = useState<Light[]>([]);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [mode, setMode] = useState('');
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [syncStatus, setSyncStatus] = useState('connecting');
  const [sceneError, setSceneError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [updated, setUpdated] = useState('');
  const [editing, setEditing] = useState<ScenePreview | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<string | null>(null);
  const locked = useRef(false);
  const alive = useRef(true);
  const pending = useRef(false);
  const streaming = useRef(false);
  async function refresh() {
    if (!streaming.current) {
      const next = await api<Light[]>('/lights');
      if (!alive.current) return;
      if (!streaming.current) {
        setLights(next); setConnectionError(''); setUpdated(new Date().toLocaleTimeString());
      }
    }
    try { const nextScenes = await api<Scene[]>('/scenes'); if (alive.current) {setScenes(nextScenes); setSceneError('');} }
    catch (e) { if (alive.current) setSceneError((e as Error).message); }
  }
  useEffect(() => {
    alive.current = true;
    let timer: ReturnType<typeof setTimeout>;
    let nextMetadata = 0;
    async function poll() {
      if (!locked.current && !pending.current && (!streaming.current || Date.now() >= nextMetadata)) {
        locked.current = true;
        try { await refresh(); const status = await api<{provider: string}>('/status'); if (alive.current) setMode(status.provider); nextMetadata = Date.now() + 30000; }
        catch (e) { if (alive.current) setConnectionError((e as Error).message); }
        finally {locked.current = false; if (alive.current) setLoading(false);}
      }
      if (alive.current) timer = setTimeout(poll, 5000);
    }
    void poll();
    return () => {alive.current = false; clearTimeout(timer);};
  }, []);
  useEffect(() => {
    let disposed = false;
    let socket: WebSocket;
    let retry: ReturnType<typeof setTimeout>;
    let delay = 1000;
    function connect() {
      if (disposed) return;
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(`${protocol}//${location.host}/api/ws`);
      socket.onmessage = event => {
        if (disposed) return;
        try {
          const state = JSON.parse(event.data);
          if (state.type !== 'state' || !Array.isArray(state.lights)) return;
          streaming.current = true;
          delay = 1000;
          setSyncStatus('live'); setLoading(false);
          if (state.available) {
            setLights(state.lights);
            setUpdated(new Date(state.updated_at).toLocaleTimeString());
            setConnectionError('');
          } else {
            setConnectionError(`Hub unavailable (${state.error_category}). Showing last known state; reconnecting automatically.`);
          }
        } catch {
          socket.close();
        }
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        streaming.current = false;
        if (disposed) return;
        setSyncStatus('reconnecting · polling fallback');
        retry = setTimeout(connect, delay);
        delay = Math.min(delay * 2, 10000);
      };
    }
    connect();
    return () => {disposed = true; streaming.current = false; clearTimeout(retry); socket?.close();};
  }, []);
  async function command(path: string, method: string, body?: unknown) {
    if (pending.current) return false;
    pending.current = true; setBusy(true); setError('');
    while (locked.current) await new Promise(resolve => setTimeout(resolve, 50));
    locked.current = true;
    try { await api(path, method, body); await refresh(); return true; }
    catch (e) { setError((e as Error).message); return false; }
    finally {locked.current = false; pending.current = false; setBusy(false);}
  }
  async function preview(id: string) {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError('');
    while (locked.current) await new Promise(resolve => setTimeout(resolve, 50));
    locked.current = true;
    try {setEditing(await api<ScenePreview>(`/scenes/${encodeURIComponent(id)}`));}
    catch (error) {setError((error as Error).message);}
    finally {locked.current = false; pending.current = false; setBusy(false);}
  }
  const rooms = Array.from(new Set(lights.map(light => light.room_id)));
  return <main>
    <header><h1>ikealternative_</h1><p>your lights.</p></header>
    <p className="meta" role="status">{loading ? 'connecting…' : `${mode === 'mock' ? 'mock · simulated lights' : mode === 'dirigera' ? 'dirigera · local hub' : 'disconnected'} · ${syncStatus}${updated ? ` · last synced ${updated}` : ''}`}{busy ? ' · applying…' : ''}</p>
    {error && <p className="error" role="alert">{error} Check current state before retrying.</p>}
    {connectionError && <p className="error" role="alert">{connectionError}</p>}
    {!loading && !error && !connectionError && lights.length === 0 && <p>No lights found on this hub.</p>}
    <ColorControls lights={lights} busy={busy || loading} apply={body => command('/lights/color', 'PUT', body)}/>
    <div aria-busy={busy || loading}>
      {rooms.map(roomId => {
        const members = lights.filter(light => light.room_id === roomId);
        const dimmable = members.filter(light => light.dimmable && light.brightness !== null);
        const roomPath = `/rooms/${encodeURIComponent(roomId)}`;
        return <section key={roomId} aria-label={members[0].room}>
          <h2>{members[0].room.toLowerCase()} /</h2>
          <div className="room-controls"><span>all lights</span><button disabled={busy || loading} onClick={() => void command(`${roomPath}/power`, 'PUT', {is_on: true})}>on</button><button disabled={busy || loading} onClick={() => void command(`${roomPath}/power`, 'PUT', {is_on: false})}>off</button></div>
          {dimmable.length > 0 && <Slider label={`${members[0].room} brightness`} value={Math.round(dimmable.reduce((n,l) => n + (l.brightness ?? 1), 0)/dimmable.length)} disabled={busy || loading} commit={brightness => void command(`${roomPath}/brightness`, 'PUT', {brightness})}/>}
          <ul>{members.map(light => <li key={light.id}>
            <div className="light-heading"><span>{light.name}{light.reachable === false && <small> · unreachable</small>}</span><button aria-label={`${light.name} power`} aria-pressed={light.is_on} disabled={busy || loading || light.reachable === false} onClick={() => void command(`/lights/${encodeURIComponent(light.id)}/power`, 'PUT', {is_on: !light.is_on})}>{light.is_on ? '[ on ]' : '[ off ]'}</button></div>
            {light.dimmable && light.brightness !== null && <Slider label={`${light.name} brightness`} value={light.brightness} disabled={busy || loading || light.reachable === false} commit={brightness => void command(`/lights/${encodeURIComponent(light.id)}/brightness`, 'PUT', {brightness})}/>}
          </li>)}</ul>
        </section>;
      })}
    </div>
    <section aria-labelledby="scenes"><h2 id="scenes">scenes /</h2>
      {sceneError && <p role="alert" className="error">Scenes: {sceneError}</p>}
      {!loading && !sceneError && scenes.length === 0 && <p>No native scenes yet.</p>}
      <p className="meta">Scene buttons run immediately. Daily schedules are enabled when saved and run automatically; these buttons do not enable or arm them.</p>
      {(['instant', 'gradual'] as const).map(kind => <div className="scene-group" key={kind}>
        <h3>{kind} scenes</h3>
        {scenes.filter(scene => (scene.kind ?? 'instant') === kind).length === 0 && <p className="meta">No {kind} scenes.</p>}
        {scenes.filter(scene => (scene.kind ?? 'instant') === kind).map(scene => <div className="scene-row" key={scene.id}>
          <div><span>{scene.name}</span>{scene.daily_time && <small> · daily {scene.daily_time} (hub time)</small>}
            {scene.next_run && <p className="meta">next run: {new Date(scene.next_run).toLocaleString()} (your time)</p>}</div>
          <div className="scene-actions">
            <button disabled={busy || loading} aria-label={`Run ${scene.name} now`} onClick={() => void command(`/scenes/${encodeURIComponent(scene.id)}/activate`, 'POST')}>run now</button>
            <button disabled={busy || loading} aria-label={`Preview or edit ${scene.name}`} onClick={() => void preview(scene.id)}>preview / edit</button>
            <button disabled={busy || loading} aria-label={`Delete ${scene.name}`} onClick={() => setDeleteCandidate(scene.id)}>delete</button>
          </div>
          {deleteCandidate === scene.id && <div className="delete-confirm" role="group" aria-label={`Confirm deletion of ${scene.name}`}>
            <p>Delete “{scene.name}” from DIRIGERA and IKEA Home, including its schedule?</p>
            <button disabled={busy} onClick={() => void command(`/scenes/${encodeURIComponent(scene.id)}`, 'DELETE').then(ok => {
              if (ok) {setDeleteCandidate(null); if (editing?.id === scene.id) setEditing(null);}
            })}>confirm delete</button> <button disabled={busy} onClick={() => setDeleteCandidate(null)}>cancel</button>
          </div>}
        </div>)}
      </div>)}
      <SceneEditor lights={lights} busy={busy || loading} mock={mode === 'mock'} editing={editing} cancel={() => setEditing(null)}
        save={scene => command(editing ? `/scenes/${encodeURIComponent(editing.id)}` : '/scenes', editing ? 'PUT' : 'POST', scene)}/>
    </section>
    <footer>local first. a little less friction.</footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<App/>);
