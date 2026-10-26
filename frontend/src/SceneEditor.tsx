import { useEffect, useRef, useState } from 'react';
import { hsv, hexFromHsv } from './colors';

export type SceneLight = {
  id: string; name: string; dimmable: boolean;
  supports_color: boolean; supports_temperature: boolean;
  color_temperature_min: number | null; color_temperature_max: number | null;
  color_temperature: number | null; color_hue: number | null; color_saturation: number | null;
  brightness: number | null;
};
type Kind = 'fixed' | 'fade' | 'daily' | 'snapshot';
type Settings = {is_on: boolean; brightness?: number; temperature?: number; hue?: number; saturation?: number};
export type SceneRequest = {name: string; light_ids: string[]; state?: Settings;
  fade?: {duration_seconds: number; start: Settings}; daily_time?: string};
export type ScenePreview = {id: string; name: string; definition: SceneRequest | null;
  editable: boolean; reason: string | null; targets: object[]};

export function SceneEditor({lights, busy, mock, save, editing, cancel}: {
  lights: SceneLight[]; busy: boolean; mock: boolean; save: (scene: SceneRequest) => Promise<boolean>;
  editing: ScenePreview | null; cancel: () => void;
}) {
  const [name, setName] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [kind, setKind] = useState<Kind>('fixed');
  const [power, setPower] = useState(true);
  const [useBrightness, setUseBrightness] = useState(true);
  const [endBrightness, setEndBrightness] = useState(70);
  const [startBrightness, setStartBrightness] = useState(20);
  const [colorMode, setColorMode] = useState('temperature');
  const [temperature, setTemperature] = useState(2700);
  const [startTemperature, setStartTemperature] = useState(4000);
  const [color, setColor] = useState('#ff9933');
  const [minutes, setMinutes] = useState(10);
  const [scheduled, setScheduled] = useState(false);
  const [time, setTime] = useState('22:00');
  const [saved, setSaved] = useState('');
  const details = useRef<HTMLDetailsElement>(null);
  const members = lights.filter(light => selected.includes(light.id));
  const allDimmable = members.length > 0 && members.every(light => light.dimmable);
  const allColor = members.length > 0 && members.every(light => light.supports_color);
  const allTemperature = members.length > 0 && members.every(light => light.supports_temperature && light.color_temperature_min !== null && light.color_temperature_max !== null);
  const lower = allTemperature ? Math.max(...members.map(light => light.color_temperature_min!)) : 1800;
  const upper = allTemperature ? Math.min(...members.map(light => light.color_temperature_max!)) : 6500;
  useEffect(() => {
    if (!editing && lower <= upper) {
      setTemperature(previous => Math.max(lower, Math.min(upper, previous)));
      setStartTemperature(previous => Math.max(lower, Math.min(upper, previous)));
    }
  }, [lower, upper, editing]);
  const selectionKey = members.map(light => light.id).join(',');
  useEffect(() => {
    if (editing || !members.length) return;
    const current = members[0];
    setTemperature(current.color_temperature ?? 2700);
    setStartTemperature(current.color_temperature ?? 2700);
    setEndBrightness(current.brightness ?? 70);
    setStartBrightness(current.brightness ?? 70);
    setColor(hexFromHsv(current.color_hue ?? 0, current.color_saturation ?? 0));
    // Seed once when selection changes, without overwriting drafts on every live poll.
  }, [selectionKey, editing]);
  useEffect(() => {
    setSaved('');
    if (!editing) {setName(''); setSelected([]); setKind('fixed'); setColorMode('temperature'); setScheduled(false); return;}
    if (details.current) {details.current.open = true; details.current.scrollIntoView({behavior: 'smooth', block: 'start'});}
    const definition = editing.definition;
    setName(editing.name);
    if (!definition) return;
    setSelected(definition.light_ids);
    const end = definition.state;
    const start = definition.fade?.start;
    setKind(definition.fade ? 'fade' : definition.daily_time && end && Object.values(end).filter(value => value != null).length === 1 ? 'daily' : 'fixed');
    setPower(end?.is_on ?? true);
    setUseBrightness(end?.brightness != null);
    setEndBrightness(end?.brightness ?? 70); setStartBrightness(start?.brightness ?? end?.brightness ?? 70);
    const current = lights.find(light => definition.light_ids.includes(light.id));
    setTemperature(end?.temperature ?? current?.color_temperature ?? 2700);
    setStartTemperature(start?.temperature ?? end?.temperature ?? current?.color_temperature ?? 2700);
    setColorMode(end?.hue != null ? 'rgb' : 'temperature');
    setColor(hexFromHsv(end?.hue ?? current?.color_hue ?? 0, end?.saturation ?? current?.color_saturation ?? 0));
    setMinutes((definition.fade?.duration_seconds ?? 600) / 60);
    setScheduled(Boolean(definition.daily_time)); setTime(definition.daily_time ?? '22:00');
  }, [editing]);
  const custom = kind === 'fixed' || kind === 'fade';
  const fadeChanges = (useBrightness && startBrightness !== endBrightness) || (colorMode === 'temperature' && startTemperature !== temperature);
  const capabilityError = custom && (
    (useBrightness && !allDimmable) ||
    (colorMode === 'temperature' && (!allTemperature || lower > upper)) ||
    (colorMode === 'rgb' && !allColor));
  const invalid = (editing && !editing.editable) || !name.trim() || members.length === 0 || capabilityError ||
    (custom && colorMode === 'temperature' && (temperature < lower || temperature > upper || (kind === 'fade' && (startTemperature < lower || startTemperature > upper)))) ||
    (kind === 'fade' && (!fadeChanges || !Number.isFinite(minutes) || minutes <= 0 || minutes > 1440));

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (invalid) return;
    setSaved('');
    const request: SceneRequest = {name: name.trim(), light_ids: members.map(light => light.id)};
    if (kind === 'daily') request.state = {is_on: power};
    if (custom) {
      request.state = {is_on: power};
      if (useBrightness) request.state.brightness = endBrightness;
      if (colorMode === 'temperature') request.state.temperature = temperature;
      if (colorMode === 'rgb') Object.assign(request.state, hsv(color));
      if (kind === 'fade') {
        const start: Settings = {is_on: true};
        if (useBrightness) start.brightness = startBrightness;
        if (colorMode === 'temperature') start.temperature = startTemperature;
        request.fade = {duration_seconds: Math.round(minutes * 60), start};
      }
    }
    if (kind === 'daily' || scheduled) request.daily_time = time;
    if (await save(request)) {
      setSaved(request.daily_time
        ? `Saved “${request.name}”. Daily schedule enabled for ${time} in the hub’s timezone. No activation click is needed; “run now” runs immediately.`
        : `Saved “${request.name}”. Activate it using its scene button.`);
    }
  }

  return <details ref={details} className="scene-editor"><summary>{editing ? `preview / edit: ${editing.name}` : 'create a scene'}</summary>
    <p>Apply the same settings to selected lights. Scenes are saved on DIRIGERA; saving a manual scene does not run it.</p>
    {editing && <><p className="meta">Previewing settings does not change lights. Saving updates this scene in place.</p><button disabled={busy} onClick={cancel}>close preview / new scene</button></>}
    {editing && !editing.editable && <><p role="status">{editing.reason}</p><pre className="scene-preview">{JSON.stringify(editing.targets, null, 2)}</pre></>}
    <form onSubmit={event => void submit(event)}>
      <fieldset disabled={busy || Boolean(editing && !editing.editable)}>
        <label className="editor-field">scene name <input required maxLength={80} value={name} onChange={event => setName(event.target.value)}/></label>
        <fieldset><legend>include lights</legend>{lights.map(light => <label className="checkbox" key={light.id}>
          <input type="checkbox" checked={selected.includes(light.id)} onChange={event => setSelected(previous => event.target.checked ? [...previous, light.id] : previous.filter(id => id !== light.id))}/>{light.name}
        </label>)}</fieldset>
        <label className="editor-field">behavior <select value={kind} onChange={event => {
          const next = event.target.value as Kind; setKind(next);
          if (next === 'fade' && colorMode === 'rgb') setColorMode('temperature');
        }}>
          <option value="fixed">fixed brightness / color</option>
          <option value="fade">fade over time</option>
          <option value="daily">daily on / off</option>
          <option value="snapshot">capture current on / off states</option>
        </select></label>
        {kind !== 'snapshot' && <label className="editor-field">{kind === 'fade' ? 'power at end' : 'power'}
          <select value={power ? 'on' : 'off'} onChange={event => setPower(event.target.value === 'on')}>
            <option value="on">on</option><option value="off">off</option>
          </select></label>}
        {custom && <>
          <label className="checkbox"><input type="checkbox" checked={useBrightness} onChange={event => setUseBrightness(event.target.checked)}/>set brightness</label>
          {useBrightness && <div className="editor-grid">
            {kind === 'fade' && <label>start brightness (%)<input type="number" required min="1" max="100" value={startBrightness} onChange={event => setStartBrightness(Number(event.target.value))}/></label>}
            <label>{kind === 'fade' ? 'end brightness (%)' : 'brightness (%)'}<input type="number" required min="1" max="100" value={endBrightness} onChange={event => setEndBrightness(Number(event.target.value))}/></label>
          </div>}
          <label className="editor-field">color <select value={colorMode} onChange={event => setColorMode(event.target.value)}>
            <option value="temperature">white temperature</option>
            {kind !== 'fade' && <option value="rgb">RGB color</option>}
          </select></label>
          {colorMode === 'temperature' && <><p className="meta">Lower Kelvin is warmer; higher is cooler. Shared supported range: {lower}–{upper} K.</p>
            <div className="editor-grid">
              {kind === 'fade' && <label>start temperature (K)<input type="number" required min={lower} max={upper} value={startTemperature} onChange={event => setStartTemperature(Number(event.target.value))}/></label>}
              <label>{kind === 'fade' ? 'end temperature (K)' : 'temperature (K)'}<input type="number" required min={lower} max={upper} value={temperature} onChange={event => setTemperature(Number(event.target.value))}/></label>
            </div></>}
          {colorMode === 'rgb' && <label className="editor-field">color hue / saturation <input aria-label="Scene color" type="color" value={color} onChange={event => setColor(event.target.value)}/></label>}
          {kind === 'fade' && <><label className="editor-field">duration (minutes)<input type="number" required min="0.1" max="1440" step="0.1" value={minutes} onChange={event => setMinutes(Number(event.target.value))}/></label>
            <p className="meta">Equal start/end temperatures keep temperature constant. Starts with lights on; choose “off” at the end for a bedtime fade. RGB fades are not supported.</p></>}
        </>}
        {kind !== 'daily' && <label className="checkbox"><input type="checkbox" checked={scheduled} onChange={event => setScheduled(event.target.checked)}/>also run daily</label>}
        {(kind === 'daily' || scheduled) && <><label className="editor-field">daily time <input type="time" required value={time} onChange={event => setTime(event.target.value)}/></label>
          <p className="meta">Every day at this time in the hub’s configured timezone. Saving enables the schedule; DIRIGERA runs it even when the website is closed.</p>
          {mock && <p className="meta">Mock mode stores this time but does not run daily schedules. Manual activation and fades are simulated.</p>}
        </>}
        {members.length > 0 && capabilityError && <p role="alert" className="error">Some selected lights do not support these settings. Change the selection or leave that setting unchanged.</p>}
        {kind === 'fade' && !fadeChanges && <p className="meta">Choose a different start and end brightness or temperature.</p>}
        <button disabled={busy || Boolean(invalid)}>{editing ? 'save changes' : 'save scene'}</button>
      </fieldset>
    </form>
    {saved && <p role="status">{saved}</p>}
  </details>;
}
