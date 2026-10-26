"""Scene creation tests are pure/mock only: no physical hub mutations."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import create_app
from app.models import SceneCreate, SceneState
from app.providers import DirigeraProvider, MockProvider


@pytest.fixture
def client():
    with TestClient(create_app(MockProvider())) as client:
        yield client


def test_fixed_color_scene_creation_does_not_activate(client):
    before = client.get('/api/lights').json()
    response = client.post('/api/scenes', json={
        'name': 'Reading', 'light_ids': ['desk', 'floor'],
        'state': {'is_on': True, 'brightness': 55, 'hue': 240, 'saturation': .8}})
    assert response.status_code == 201
    assert client.get('/api/lights').json() == before
    assert client.post(f"/api/scenes/{response.json()['id']}/activate").status_code == 204
    lights = client.get('/api/lights').json()
    assert all(light['brightness'] == 55 and light['color_hue'] == 240 and light['color_saturation'] == .8 for light in lights[:2])
    assert lights[2] == before[2]


def test_temperature_only_scene_preserves_brightness(client):
    response = client.post('/api/scenes', json={
        'name': 'Warm', 'light_ids': ['desk'], 'state': {'is_on': True, 'temperature': 2200}})
    assert response.status_code == 201
    client.post(f"/api/scenes/{response.json()['id']}/activate")
    light = client.get('/api/lights').json()[0]
    assert light['color_temperature'] == 2200
    assert light['brightness'] == 70


@pytest.mark.parametrize('state', [
    {'brightness': 0}, {'brightness': 101}, {'temperature': 999},
    {'hue': 361, 'saturation': .5}, {'hue': 20}, {'saturation': .5},
    {'hue': 20, 'saturation': 2}, {'hue': 20, 'saturation': .5, 'temperature': 2500},
    {'is_on': 'yes'}, {'unexpected': True},
])
def test_invalid_scene_state_rejected_before_execution(client, state):
    response = client.post('/api/scenes', json={'name': 'Bad', 'light_ids': ['desk'], 'state': state})
    assert response.status_code == 422
    assert client.get('/api/commands').json() == []


@pytest.mark.parametrize('time', ['24:00', '12:60', '9:00', 'noon', '12:00:00', ''])
def test_invalid_daily_time(client, time):
    assert client.post('/api/scenes', json={'name': 'Daily', 'light_ids': ['desk'],
        'state': {'is_on': False}, 'daily_time': time}).status_code == 422


def test_daily_time_stored_without_mock_scheduler():
    provider = MockProvider()
    scene = provider.create_scene(SceneCreate(name='Daily off', light_ids=['desk'],
        state=SceneState(is_on=False), daily_time='23:15'))
    assert provider.custom_scenes[scene.id][0].daily_time == '23:15'
    assert provider.list_lights()[0].is_on is True
    provider.activate_scene(scene.id)
    assert provider.list_lights()[0].is_on is False


def test_mock_fade_interpolates_brightness_and_temperature_and_turns_off():
    now = [0.0]
    provider = MockProvider(clock=lambda: now[0])
    scene = provider.create_scene(SceneCreate.model_validate({
        'name': 'Bedtime', 'light_ids': ['desk'],
        'state': {'is_on': False, 'brightness': 10, 'temperature': 2200},
        'fade': {'duration_seconds': 600, 'start': {'is_on': True, 'brightness': 80, 'temperature': 4000}}}))
    provider.activate_scene(scene.id)
    start = provider.list_lights()[0]
    assert (start.brightness, start.color_temperature, start.is_on) == (80, 4000, True)
    now[0] = 300
    middle = provider.list_lights()[0]
    assert (middle.brightness, middle.color_temperature, middle.is_on) == (45, 3100, True)
    now[0] = 600
    end = provider.list_lights()[0]
    assert (end.brightness, end.color_temperature, end.is_on) == (10, 2200, False)
    assert not provider.transitions


def test_manual_mock_control_cancels_fade():
    now = [0.0]
    provider = MockProvider(clock=lambda: now[0])
    scene = provider.create_scene(SceneCreate.model_validate({
        'name': 'Fade', 'light_ids': ['desk'], 'state': {'brightness': 10},
        'fade': {'duration_seconds': 60, 'start': {'brightness': 80}}}))
    provider.activate_scene(scene.id)
    provider.set_brightness('desk', 42)
    now[0] = 60
    assert provider.list_lights()[0].brightness == 42


@pytest.mark.parametrize('body', [
    {'fade': {'duration_seconds': 60, 'start': {'brightness': 1}}},
    {'state': {'brightness': 80}, 'fade': {'duration_seconds': 0, 'start': {'brightness': 1}}},
    {'state': {'brightness': 80}, 'fade': {'duration_seconds': 60, 'start': {'is_on': False, 'brightness': 1}}},
    {'state': {'brightness': 80}, 'fade': {'duration_seconds': 60, 'start': {'temperature': 2200}}},
    {'state': {'brightness': 80}, 'fade': {'duration_seconds': 60, 'start': {'brightness': 80}}},
    {'state': {'hue': 30, 'saturation': 1}, 'fade': {'duration_seconds': 60, 'start': {'hue': 240, 'saturation': 1}}},
])
def test_invalid_fade_definitions(body):
    with pytest.raises(ValidationError):
        SceneCreate.model_validate({'name': 'Bad', 'light_ids': ['desk'], **body})


def fake_provider():
    provider = DirigeraProvider.__new__(DirigeraProvider)
    provider.hub = Mock()
    provider.hub.post.return_value = {'id': 'created'}
    provider.list_lights = Mock(return_value=MockProvider().list_lights())
    return provider


def test_native_fixed_scene_and_daily_schedule_payload():
    provider = fake_provider()
    scene = provider.create_scene(SceneCreate.model_validate({
        'name': 'Daily reading', 'light_ids': ['desk', 'desk', 'floor'],
        'state': {'is_on': True, 'brightness': 60, 'temperature': 3000}, 'daily_time': '18:30'}))
    assert scene.id == 'created'
    provider.list_lights.assert_called_once_with()
    args = provider.hub.post.call_args.kwargs
    assert args['route'] == '/scenes/'
    payload = args['data']
    assert payload['type'] == 'userScene'
    assert len(payload['actions']) == 2
    assert payload['actions'][0]['attributes'] == {'isOn': True, 'lightLevel': 60, 'colorTemperature': 3000}
    trigger = payload['triggers'][0]
    assert trigger['type'] == 'time' and trigger['disabled'] is False
    assert trigger['trigger']['time'] == '18:30'
    assert len(trigger['trigger']['days']) == 7
    assert provider.hub.post.call_count == 1  # Saves only; no trigger request.


@pytest.mark.parametrize('start,end,scene_type', [(80, 1, 'windDownScene'), (1, 80, 'wakeUpScene')])
def test_native_transition_payload(start, end, scene_type):
    provider = fake_provider()
    provider.create_scene(SceneCreate.model_validate({
        'name': 'Transition', 'light_ids': ['desk'], 'state': {'brightness': end, 'temperature': 2200},
        'fade': {'duration_seconds': 600, 'start': {'brightness': start, 'temperature': 4000}}}))
    payload = provider.hub.post.call_args.kwargs['data']
    assert payload['type'] == scene_type
    assert payload['actions'] == []
    command = payload['commands'][0]['commands'][0]
    assert command == {'type': 'transitionLight', 'duration': 600,
        'startState': {'isOn': True, 'lightLevel': start, 'colorTemperature': 4000},
        'endState': {'isOn': True, 'lightLevel': end, 'colorTemperature': 2200}}


def test_unsupported_or_out_of_range_target_prevents_all_writes():
    provider = fake_provider()
    provider.list_lights.return_value[1].supports_color = False
    with pytest.raises(ValueError):
        provider.create_scene(SceneCreate.model_validate({'name': 'Color', 'light_ids': ['desk', 'floor'],
            'state': {'hue': 20, 'saturation': .5}}))
    provider.hub.post.assert_not_called()
    with pytest.raises(ValueError):
        provider.create_scene(SceneCreate.model_validate({'name': 'Too warm', 'light_ids': ['desk'],
            'state': {'temperature': 1500}}))
    provider.hub.post.assert_not_called()


def test_temperature_bounds_normalized_from_mireds_and_reversed_kelvin():
    for bounds in [(153, 454), (6500, 1800)]:
        light = NS(id='bulb', room=None, is_reachable=True,
            capabilities=NS(can_receive=['isOn', 'colorTemperature', 'colorHue', 'colorSaturation']),
            attributes=NS(custom_name='Bulb', is_on=True, light_level=None,
                color_temperature=2700, color_hue=None, color_saturation=None,
                color_temperature_min=bounds[0], color_temperature_max=bounds[1]))
        normalized = DirigeraProvider.normalize(light)
        assert normalized.supports_color and normalized.supports_temperature
        assert normalized.color_temperature_min < normalized.color_temperature_max
        assert normalized.color_temperature_min >= 1800
