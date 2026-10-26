"""Protocol-neutral state access. No HTTP or persistence concerns here."""
from typing import Protocol
from urllib.parse import quote
from time import monotonic
import requests
from pydantic import AliasPath, BaseModel, Field, TypeAdapter
from .models import Light, Scene, SceneCreate, SceneDetails, SceneState
from .scene_settings import native_payload, attributes, scene_details


class _HubScene(BaseModel):
    """Read only the fields we use; hub scene types/icons evolve independently."""
    id: str = Field(min_length=1)
    name: str = Field(validation_alias=AliasPath('info', 'name'))


_hub_scene_list = TypeAdapter(list[_HubScene])


class _SceneReceipt(BaseModel):
    id: str = Field(min_length=1)


class NotFound(Exception):
    pass


class Unavailable(Exception):
    pass


class Unsupported(Exception):
    pass


class SmartHomeProvider(Protocol):
    def list_lights(self) -> list[Light]: ...
    def set_power(self, light_id: str, is_on: bool) -> None: ...
    def set_brightness(self, light_id: str, brightness: int) -> None: ...
    def list_scenes(self) -> list[Scene]: ...
    def activate_scene(self, scene_id: str) -> None: ...
    def create_scene(self, scene: SceneCreate) -> Scene: ...
    def get_scene(self, scene_id: str) -> SceneDetails: ...
    def update_scene(self, scene_id: str, scene: SceneCreate) -> Scene: ...
    def delete_scene(self, scene_id: str) -> None: ...
    def set_color(self, light_id: str, color) -> None: ...


class MockProvider:
    def __init__(self, clock=monotonic):
        self.clock = clock
        self.transitions = {}
        self.custom_scenes = {}
        self.lights = [
            Light(id='desk', name='Desk light', room_id='study', room='Study', is_on=True, brightness=70, reachable=True),
            Light(id='floor', name='Floor lamp', room_id='study', room='Study', is_on=False, brightness=40, reachable=True),
            Light(id='bed', name='Bedside', room_id='bedroom', room='Bedroom', is_on=True, brightness=25, reachable=True),
        ]
        self.scenes = {'evening': ('Evening', {'desk': (True, 30), 'floor': (True, 20), 'bed': (False, 25)})}
        for light in self.lights:
            light.supports_color = light.supports_temperature = True
            light.color_temperature_min, light.color_temperature_max = 1800, 6500
            light.color_temperature = 2700

    def list_lights(self):
        self._advance_transitions()
        return [light.model_copy(deep=True) for light in self.lights]

    def _apply(self, id_, values):
        light = self._light(id_)
        fields = {'isOn': 'is_on', 'lightLevel': 'brightness', 'colorTemperature': 'color_temperature',
                  'colorHue': 'color_hue', 'colorSaturation': 'color_saturation'}
        for key, value in values.items():
            setattr(light, fields[key], value)

    def _advance_transitions(self):
        for id_, (started, duration, start, end) in list(self.transitions.items()):
            fraction = min(1, max(0, (self.clock() - started) / duration))
            values = {'isOn': end['isOn'] if fraction == 1 else True}
            for key in ('lightLevel', 'colorTemperature'):
                if key in end:
                    values[key] = round(start[key] + fraction * (end[key] - start[key]))
            self._apply(id_, values)
            if fraction == 1:
                del self.transitions[id_]

    def _light(self, light_id):
        for light in self.lights:
            if light.id == light_id:
                return light
        raise NotFound('Light not found.')

    def set_power(self, light_id, is_on):
        self._light(light_id).is_on = is_on
        self.transitions.pop(light_id, None)

    def set_brightness(self, light_id, brightness):
        if not 1 <= brightness <= 100:
            raise ValueError('Brightness must be between 1 and 100.')
        self._light(light_id).brightness = brightness
        self.transitions.pop(light_id, None)

    def list_scenes(self):
        return [Scene(id=id_, name=value[0], kind='gradual' if id_ in self.custom_scenes and self.custom_scenes[id_][0].fade else 'instant',
            daily_time=self.custom_scenes[id_][0].daily_time if id_ in self.custom_scenes else None) for id_, value in self.scenes.items()]

    def set_color(self, light_id, color):
        light = self._light(light_id)
        values = attributes(light, SceneState(**color.model_dump()))
        values.pop('isOn')
        self._apply(light_id, values)
        self.transitions.pop(light_id, None)

    def get_scene(self, scene_id):
        if scene_id not in self.scenes:
            raise NotFound('Scene not found.')
        if scene_id in self.custom_scenes:
            definition, _ = self.custom_scenes[scene_id]
            return SceneDetails(id=scene_id, name=definition.name,
                kind='gradual' if definition.fade else 'instant', daily_time=definition.daily_time,
                definition=definition.model_copy(deep=True))
        name, targets = self.scenes[scene_id]
        return scene_details({'id': scene_id, 'info': {'name': name}, 'actions': [
            {'id': id_, 'type': 'device', 'attributes': {'isOn': power, 'lightLevel': brightness}}
            for id_, (power, brightness) in targets.items()]})

    def update_scene(self, scene_id, scene):
        if scene_id not in self.scenes:
            raise NotFound('Scene not found.')
        lights = [self._light(id_) for id_ in dict.fromkeys(scene.light_ids)]
        _, targets = native_payload(scene, lights)
        self.custom_scenes[scene_id] = (scene.model_copy(deep=True), targets)
        self.scenes[scene_id] = (scene.name, {})
        return Scene(id=scene_id, name=scene.name, kind='gradual' if scene.fade else 'instant', daily_time=scene.daily_time)

    def delete_scene(self, scene_id):
        if scene_id not in self.scenes:
            raise NotFound('Scene not found.')
        del self.scenes[scene_id]
        self.custom_scenes.pop(scene_id, None)

    def activate_scene(self, scene_id):
        if scene_id not in self.scenes:
            raise NotFound('Scene not found.')
        if scene_id in self.custom_scenes:
            definition, targets = self.custom_scenes[scene_id]
            for id_, start, end in targets:
                self.transitions.pop(id_, None)
                if definition.fade:
                    self._apply(id_, start)
                    self.transitions[id_] = (self.clock(), definition.fade.duration_seconds, start, end)
                else:
                    self._apply(id_, end)
            return
        for id_, (power, brightness) in self.scenes[scene_id][1].items():
            self.set_power(id_, power)
            if brightness is not None:
                self.set_brightness(id_, brightness)

    def create_scene(self, scene):
        from uuid import uuid4
        self._advance_transitions()
        lights = [self._light(id_) for id_ in dict.fromkeys(scene.light_ids)]
        _, targets = native_payload(scene, lights)
        id_ = str(uuid4())
        self.scenes[id_] = (scene.name, {light.id: (light.is_on, None) for light in lights})
        self.custom_scenes[id_] = (scene.model_copy(deep=True), targets)
        return Scene(id=id_, name=scene.name, kind='gradual' if scene.fade else 'instant', daily_time=scene.daily_time)


class DirigeraProvider:
    def __init__(self, ip: str, token: str):
        from dirigera import Hub
        self.hub = Hub(token=token, ip_address=ip)

    @staticmethod
    def normalize(light):
        attrs = light.attributes
        bounds = [getattr(attrs, key, None) for key in ('color_temperature_min', 'color_temperature_max')]
        bounds = [round(1_000_000 / value) if 0 < value < 1000 else value for value in bounds if value is not None]
        capabilities = light.capabilities.can_receive
        return Light(
            id=light.id, name=attrs.custom_name,
            room_id=light.room.id if light.room else 'unassigned',
            room=light.room.name if light.room else 'Unassigned',
            is_on=attrs.is_on, brightness=attrs.light_level,
            reachable=light.is_reachable,
            dimmable='lightLevel' in light.capabilities.can_receive,
            color_temperature=attrs.color_temperature, color_hue=attrs.color_hue,
            color_saturation=attrs.color_saturation,
            supports_color='colorHue' in capabilities and 'colorSaturation' in capabilities,
            supports_temperature='colorTemperature' in capabilities,
            color_temperature_min=min(bounds) if len(bounds) == 2 else None,
            color_temperature_max=max(bounds) if len(bounds) == 2 else None,
        )

    def list_lights(self):
        return [self.normalize(light) for light in self.hub.get_lights()]

    def _light(self, light_id):
        # Discovery validates IDs before handing them to library URL construction.
        for light in self.hub.get_lights():
            if light.id == light_id:
                return light
        raise NotFound('Light not found.')

    def set_power(self, light_id, is_on):
        light = self._light(light_id)
        if 'isOn' not in light.capabilities.can_receive:
            raise Unsupported('This light does not support power control.')
        light.set_light(is_on)

    def set_brightness(self, light_id, brightness):
        light = self._light(light_id)
        if 'lightLevel' not in light.capabilities.can_receive:
            raise Unsupported('This light does not support dimming.')
        light.set_light_level(brightness)

    def set_color(self, light_id, color):
        light = self._light(light_id)
        attributes(self.normalize(light), SceneState(**color.model_dump()))
        if color.temperature is not None:
            light.set_color_temperature(color.temperature)
        else:
            light.set_light_color(color.hue, color.saturation)

    def list_scenes(self):
        # The library's full Scene parser rejects newer types such as windDownScene.
        # Its transport still supports this endpoint; validate our smaller contract.
        raw = self.hub.get('/scenes')
        _hub_scene_list.validate_python(raw)
        details = [scene_details(item) for item in raw]
        return [Scene(id=item.id, name=item.name, kind=item.kind,
                      daily_time=item.daily_time, next_run=item.next_run) for item in details]

    def _raw_scene(self, scene_id):
        raw = self.hub.get('/scenes')
        scenes = _hub_scene_list.validate_python(raw)
        for parsed, item in zip(scenes, raw):
            if parsed.id == scene_id:
                return item
        raise NotFound('Scene not found.')

    def get_scene(self, scene_id):
        return scene_details(self._raw_scene(scene_id))

    def delete_scene(self, scene_id):
        self._raw_scene(scene_id)
        self.hub.delete(route=f'/scenes/{quote(scene_id, safe="")}')

    def update_scene(self, scene_id, scene):
        original = self._raw_scene(scene_id)
        if not scene_details(original).editable:
            raise Unsupported('This native scene cannot be safely represented by the shared editor.')
        discovered = {light.id: light for light in self.list_lights()}
        if any(id_ not in discovered for id_ in scene.light_ids):
            raise NotFound('Light not found.')
        payload, _ = native_payload(scene, [discovered[id_] for id_ in dict.fromkeys(scene.light_ids)])
        payload['info']['icon'] = original['info'].get('icon', 'scenes_home_filled')
        for trigger in original.get('triggers', []):
            if trigger.get('type') == 'app':
                payload['triggers'].append({key: trigger[key] for key in ('id', 'type', 'disabled') if key in trigger})
            elif trigger.get('type') == 'time' and payload['triggers'] and trigger.get('id'):
                payload['triggers'][0]['id'] = trigger['id']
        # Native scene replacement uses PUT, unlike the library's device PATCH.
        # https://github.com/lpgera/dirigera/blob/main/src/api/scenes.ts
        response = requests.put(f'{self.hub.api_base_url}/scenes/{quote(scene_id, safe="")}',
            headers=self.hub.headers(), json=payload, timeout=10, verify=False)
        response.raise_for_status()
        return Scene(id=scene_id, name=scene.name, kind='gradual' if scene.fade else 'instant', daily_time=scene.daily_time)

    def activate_scene(self, scene_id):
        for scene in self.list_scenes():
            if scene.id == scene_id:
                # Same endpoint as the library's Scene.trigger(), without full parsing.
                self.hub.post(route=f'/scenes/{quote(scene.id, safe="")}/trigger')
                return
        raise NotFound('Scene not found.')

    def create_scene(self, scene):
        discovered = {light.id: light for light in self.list_lights()}
        if any(id_ not in discovered for id_ in scene.light_ids):
            raise NotFound('Light not found.')
        lights = [discovered[id_] for id_ in dict.fromkeys(scene.light_ids)]
        payload, _ = native_payload(scene, lights)
        result = _SceneReceipt.model_validate(self.hub.post(route='/scenes/', data=payload))
        return Scene(id=result.id, name=scene.name.strip(), kind='gradual' if scene.fade else 'instant', daily_time=scene.daily_time)
