import { useEffect, useState } from 'react';
import type { SceneLight } from './SceneEditor';
import { hsv, hexFromHsv } from './colors';

export function ColorControls({lights, busy, apply}: {
  lights: SceneLight[]; busy: boolean; apply: (body: object) => Promise<boolean>;
}) {
  const [temperature, setTemperature] = useState(2700);
  const [color, setColor] = useState('#ffffff');
  const [editing, setEditing] = useState(false);
  const currentTemperature = lights[0]?.color_temperature ?? 2700;
  const currentHue = lights[0]?.color_hue ?? 0, currentSaturation = lights[0]?.color_saturation ?? 0;
  useEffect(() => {if (!editing) {setTemperature(currentTemperature); setColor(hexFromHsv(currentHue, currentSaturation));}},
    [currentTemperature, currentHue, currentSaturation, editing]);
  const hasTemperature = lights.length > 0 && lights.every(light => light.supports_temperature && light.color_temperature_min != null && light.color_temperature_max != null);
  const hasColor = lights.length > 0 && lights.every(light => light.supports_color);
  const lower = hasTemperature ? Math.max(...lights.map(light => light.color_temperature_min!)) : 1800;
  const upper = hasTemperature ? Math.min(...lights.map(light => light.color_temperature_max!)) : 6500;
  const mixed = lights.some(light => light.color_temperature !== lights[0]?.color_temperature || light.color_hue !== lights[0]?.color_hue || light.color_saturation !== lights[0]?.color_saturation);
  return <section aria-labelledby="all-colors"><h2 id="all-colors">all lights / color</h2>
    <p className="meta">Apply white temperature or RGB color to all {lights.length} lights. Power and brightness stay as they are.{mixed ? ' Lights currently differ; the picker starts from the first light.' : ''}</p>
    <fieldset disabled={busy}>
      {hasTemperature && <div className="color-control"><label>white temperature (K)<input type="number" min={lower} max={upper} value={temperature} onFocus={() => setEditing(true)} onChange={event => setTemperature(Number(event.target.value))}/></label>
        <button disabled={lower > upper || temperature < lower || temperature > upper} onClick={() => void apply({temperature}).then(ok => {if(ok) setEditing(false);})}>apply temperature</button></div>}
      {hasColor && <div className="color-control"><label>RGB color<input aria-label="All lights color" type="color" value={color} onChange={event => {setEditing(true); setColor(event.target.value);}}/></label>
        <button onClick={() => void apply(hsv(color)).then(ok => {if(ok) setEditing(false);})}>apply color</button></div>}
      {!hasTemperature && !hasColor && <p>No color mode is supported by every light.</p>}
    </fieldset>
  </section>;
}
