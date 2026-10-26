from threading import RLock
from .models import Room
from .providers import NotFound, Unsupported, Unavailable
from .models import SceneState
from .scene_settings import attributes


class SmartHomeService:
    """One boundary for Phase 2 metrics/events; serialize reads and mutations."""
    def __init__(self, provider):
        self.provider = provider
        self.lock = RLock()

    def rooms(self):
        rooms = {}
        for light in self.provider.list_lights():
            room = rooms.setdefault(light.room_id, Room(id=light.room_id, name=light.room, light_ids=[]))
            room.light_ids.append(light.id)
        return list(rooms.values())

    def room_control(self, room_id, operation, value):
        lights = [light for light in self.provider.list_lights() if light.room_id == room_id]
        if not lights:
            raise NotFound('Room not found.')
        if operation == 'set_brightness':
            lights = [light for light in lights if light.dimmable]
            if not lights:
                raise Unsupported('This room has no dimmable lights.')
        for light in lights:
            try:
                getattr(self.provider, operation)(light.id, value)
            except Exception as exc:
                raise Unavailable('Room command failed; some lights may already have changed. Refresh before retrying.') from exc

    def set_all_color(self, color):
        lights = self.provider.list_lights()
        if not lights:
            raise NotFound('No lights found.')
        for light in lights:
            attributes(light, SceneState(**color.model_dump()))
        for light in lights:
            try:
                self.provider.set_color(light.id, color)
            except Exception as exc:
                raise Unavailable('Group command failed; some lights may already have changed.') from exc
