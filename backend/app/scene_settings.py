"""Pure helpers shared by native scene creation and mock simulation."""
from .models import Light, SceneState


def attributes(light: Light, state: SceneState) -> dict:
    values = {'isOn': state.is_on}
    if state.brightness is not None:
        if not light.dimmable:
            raise ValueError(f'{light.name} does not support brightness.')
        values['lightLevel'] = state.brightness
    if state.temperature is not None:
        if not light.supports_temperature:
            raise ValueError(f'{light.name} does not support white temperature.')
        lower, upper = light.color_temperature_min, light.color_temperature_max
        if lower is None or upper is None:
            raise ValueError(f'{light.name} does not report a safe temperature range.')
        if not lower <= state.temperature <= upper:
            raise ValueError(f'{light.name} temperature must be within its reported range.')
        values['colorTemperature'] = state.temperature
    if state.hue is not None:
        if not light.supports_color:
            raise ValueError(f'{light.name} does not support RGB color.')
        values['colorHue'], values['colorSaturation'] = state.hue, state.saturation
    return values


def native_payload(scene, lights):
    # Prepare and validate every device before any hub mutation occurs.
    targets = []
    for light in lights:
        end = attributes(light, scene.state) if scene.state else {'isOn': light.is_on}
        start = attributes(light, scene.fade.start) if scene.fade else None
        targets.append((light.id, start, end))
    triggers = []
    if scene.daily_time:
        triggers.append({'type': 'time', 'disabled': False, 'trigger': {
            'days': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], 'time': scene.daily_time}})
    payload = {'info': {'name': scene.name.strip(), 'icon': 'scenes_home_filled'},
               'type': 'userScene', 'triggers': triggers, 'actions': []}
    if scene.fade:
        # Match the native transitionLight structure discovered on the actual hub.
        rising = any(end.get('lightLevel', 0) > start.get('lightLevel', 0) for _, start, end in targets)
        payload['type'] = 'wakeUpScene' if rising else 'windDownScene'
        payload['commands'] = [{'id': id_, 'type': 'device', 'enabled': True, 'commands': [{
            'type': 'transitionLight', 'duration': scene.fade.duration_seconds,
            'startState': start, 'endState': end}]} for id_, start, end in targets]
    else:
        payload['actions'] = [{'id': id_, 'deviceId': id_, 'type': 'device', 'enabled': True,
                               'attributes': end} for id_, _, end in targets]
    return payload, targets


def scene_details(raw):
    """Decode only scenes our editor can represent without losing native behavior."""
    from .models import SceneCreate, SceneDetails
    from pydantic import ValidationError
    fade_commands = [command for target in raw.get('commands', []) if isinstance(target, dict)
                     for command in target.get('commands', []) if isinstance(command, dict)]
    gradual = bool(fade_commands) or raw.get('type') in ('wakeUpScene', 'windDownScene')
    times = [trigger for trigger in raw.get('triggers', []) if trigger.get('type') == 'time' and not trigger.get('disabled', False)]
    result = SceneDetails(id=raw['id'], name=raw['info']['name'], kind='gradual' if gradual else 'instant',
                          daily_time=times[0].get('trigger', {}).get('time') if times else None,
                          next_run=times[0].get('nextTriggerAt') if times else None)
    mapping = {'isOn': 'is_on', 'lightLevel': 'brightness', 'colorTemperature': 'temperature',
               'colorHue': 'hue', 'colorSaturation': 'saturation'}
    try:
        triggers = raw.get('triggers', [])
        complex_triggers = any(trigger.get('type') not in ('app', 'time') for trigger in triggers) or len(times) > 1
        weekday_schedule = times and set(times[0].get('trigger', {}).get('days', [])) != {'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'}
        targets = []
        if gradual and raw.get('actions'):
            raise ValueError('This scene mixes fixed actions with transition commands.')
        sources = raw.get('commands', []) if gradual else raw.get('actions', [])
        for target in sources:
            if not isinstance(target, dict) or target.get('type') != 'device' or target.get('enabled') is False:
                raise ValueError('This scene has unsupported or disabled targets.')
            id_ = target.get('deviceId', target.get('id'))
            if gradual:
                commands = target.get('commands', [])
                if len(commands) != 1 or commands[0].get('type') != 'transitionLight':
                    raise ValueError('This scene has multiple or unsupported transition steps.')
                command = commands[0]
                start, end = command.get('startState', {}), command.get('endState', {})
                duration = command.get('duration')
            else:
                start, end, duration = None, target.get('attributes', {}), None
            if not id_ or not end or 'isOn' not in end or any(key not in mapping for key in end) or (start and any(key not in mapping for key in start)):
                raise ValueError('This scene has settings the editor cannot preserve.')
            state = {mapping[key]: value for key, value in end.items()}
            item = {'light_id': id_, 'state': state}
            if gradual:
                item['fade'] = {'duration_seconds': duration, 'start': {mapping[key]: value for key, value in start.items()}}
            targets.append(item)
        result.targets = targets
        if complex_triggers or weekday_schedule or raw.get('type', 'userScene') not in ('userScene', 'wakeUpScene', 'windDownScene'):
            raise ValueError('This scene has native behavior the editor cannot preserve.')
        if not targets:
            raise ValueError('This scene has no editable light settings.')
        first = targets[0]
        if any({key: value for key, value in target.items() if key != 'light_id'} !=
               {key: value for key, value in first.items() if key != 'light_id'} for target in targets):
            raise ValueError('Lights have different settings; this shared editor cannot safely overwrite them.')
        result.definition = SceneCreate(name=result.name, light_ids=[target['light_id'] for target in targets],
            state=first['state'], fade=first.get('fade'), daily_time=result.daily_time)
    except (ValueError, ValidationError, KeyError, TypeError):
        result.editable = False
        result.reason = 'This native scene contains differing light settings or unsupported triggers/steps. Preview is available, but editing is disabled to preserve it.'
    return result
