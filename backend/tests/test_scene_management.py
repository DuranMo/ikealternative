from copy import deepcopy
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from requests.exceptions import Timeout
from app.main import create_app
from app.models import SceneCreate, ColorChange
from app.providers import DirigeraProvider, MockProvider, NotFound, Unsupported
from app.scene_settings import scene_details


@pytest.fixture
def provider():
    return MockProvider()


@pytest.fixture
def client(provider):
    with TestClient(create_app(provider)) as client:
        yield client


def test_shared_temperature_and_rgb_preserve_power_brightness(client):
    before = client.get('/api/lights').json()
    assert client.put('/api/lights/color', json={'temperature': 2200}).status_code == 204
    after = client.get('/api/lights').json()
    assert all(light['color_temperature'] == 2200 for light in after)
    assert [(light['is_on'], light['brightness']) for light in after] == [(light['is_on'], light['brightness']) for light in before]
    assert client.put('/api/lights/color', json={'hue': 120, 'saturation': .75}).status_code == 204
    assert all(light['color_hue'] == 120 and light['color_saturation'] == .75 for light in client.get('/api/lights').json())
    records = client.get('/api/commands').json()
    assert all(row['target_type'] == 'all_lights' and row['success'] for row in records)


@pytest.mark.parametrize('body', [{}, {'temperature': 999}, {'hue': 30}, {'saturation': .5},
    {'hue': 30, 'saturation': 2}, {'temperature': 2500, 'hue': 30, 'saturation': .5}])
def test_invalid_shared_color(client, body):
    assert client.put('/api/lights/color', json=body).status_code == 422


def test_validate_all_bulbs_before_shared_color_write(client, provider):
    provider.lights[-1].supports_temperature = False
    provider.set_color = Mock()
    assert client.put('/api/lights/color', json={'temperature': 2500}).status_code == 422
    provider.set_color.assert_not_called()


def test_partial_shared_color_failure(client, provider):
    original = provider.set_color
    def fail(id_, color):
        if id_ == 'floor':
            raise Timeout('private failure')
        original(id_, color)
    provider.set_color = fail
    response = client.put('/api/lights/color', json={'temperature': 2200})
    assert response.status_code == 503
    assert 'some lights' in response.text
    assert 'private failure' not in response.text
    assert client.get('/api/commands').json()[0]['error_category'] == 'timeout'


def test_scene_preview_update_same_id_and_delete_without_activation(client):
    before = client.get('/api/lights').json()
    body = {'name': 'Reading', 'light_ids': ['desk'], 'state': {'is_on': True, 'brightness': 50, 'temperature': 2500}}
    created = client.post('/api/scenes', json=body).json()
    detail = client.get('/api/scenes/' + created['id']).json()
    assert detail['editable'] is True
    assert detail['definition']['state']['temperature'] == 2500
    assert detail['definition']['light_ids'] == ['desk']
    assert client.get('/api/lights').json() == before
    body['state']['temperature'] = 3000
    body['name'] = 'Updated reading'
    response = client.put('/api/scenes/' + created['id'], json=body)
    assert response.status_code == 200 and response.json()['id'] == created['id']
    assert len(client.get('/api/scenes').json()) == 2
    assert client.get('/api/scenes/' + created['id']).json()['definition']['state']['temperature'] == 3000
    assert client.get('/api/lights').json() == before
    assert client.delete('/api/scenes/' + created['id']).status_code == 204
    assert client.get('/api/scenes/' + created['id']).status_code == 404
    assert client.delete('/api/scenes/' + created['id']).status_code == 404
    operations = [row['command_type'] for row in client.get('/api/commands').json()]
    assert 'update_scene' in operations and 'delete_scene' in operations
    assert 'activate_scene' not in operations


def test_scene_group_metadata_and_equal_temperature_fade(client):
    scene = {'name': 'Dim only', 'light_ids': ['desk'], 'state': {'brightness': 10, 'temperature': 2700},
        'fade': {'duration_seconds': 60, 'start': {'brightness': 80, 'temperature': 2700}}, 'daily_time': '22:00'}
    result = client.post('/api/scenes', json=scene)
    assert result.status_code == 201
    summaries = client.get('/api/scenes').json()
    saved = next(item for item in summaries if item['id'] == result.json()['id'])
    assert saved['kind'] == 'gradual' and saved['daily_time'] == '22:00'
    assert summaries[0]['kind'] == 'instant'
    definition = client.get('/api/scenes/' + saved['id']).json()['definition']
    assert definition['state']['temperature'] == definition['fade']['start']['temperature']


def raw_scene():
    return {'id': 'scene/id', 'info': {'name': 'Reading', 'icon': 'scenes_book'}, 'type': 'userScene',
        'actions': [{'id': 'desk', 'deviceId': 'desk', 'type': 'device', 'enabled': True,
                     'attributes': {'isOn': True, 'lightLevel': 50, 'colorTemperature': 2700}}],
        'triggers': [{'id': 'app-id', 'type': 'app', 'disabled': False},
                     {'id': 'time-id', 'type': 'time', 'disabled': False, 'trigger': {
                         'days': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], 'time': '22:00'},
                      'nextTriggerAt': '2026-10-27T03:00:00Z'}]}


def native_provider(raw):
    provider = DirigeraProvider.__new__(DirigeraProvider)
    provider.hub = Mock()
    provider.hub.api_base_url = 'https://fake-hub/v1'
    provider.hub.headers.return_value = {'Authorization': 'test-only'}
    provider.hub.get.return_value = [raw]
    provider.list_lights = Mock(return_value=MockProvider().list_lights())
    return provider


def test_native_preview_reads_only():
    provider = native_provider(raw_scene())
    result = provider.get_scene('scene/id')
    assert result.editable and result.daily_time == '22:00'
    assert result.next_run == '2026-10-27T03:00:00Z'
    assert result.definition.state.brightness == 50
    provider.hub.post.assert_not_called()
    provider.hub.delete.assert_not_called()


def test_native_update_put_preserves_id_icon_and_trigger_ids(monkeypatch):
    provider = native_provider(raw_scene())
    put = Mock()
    monkeypatch.setattr('app.providers.requests.put', put)
    definition = provider.get_scene('scene/id').definition
    definition.name = 'Edited'
    definition.state.temperature = 3000
    result = provider.update_scene('scene/id', definition)
    assert result.id == 'scene/id'
    args = put.call_args
    assert args.args == ('https://fake-hub/v1/scenes/scene%2Fid',)
    payload = args.kwargs['json']
    assert payload['info'] == {'name': 'Edited', 'icon': 'scenes_book'}
    assert payload['actions'][0]['attributes']['colorTemperature'] == 3000
    assert payload['triggers'][0]['id'] == 'time-id'
    assert payload['triggers'][1]['id'] == 'app-id'
    put.return_value.raise_for_status.assert_called_once_with()
    provider.hub.post.assert_not_called()
    provider.hub.delete.assert_not_called()


def test_native_delete_checks_id_and_encodes_url():
    provider = native_provider(raw_scene())
    provider.delete_scene('scene/id')
    provider.hub.delete.assert_called_once_with(route='/scenes/scene%2Fid')
    provider.hub.delete.reset_mock()
    with pytest.raises(NotFound):
        provider.delete_scene('missing')
    provider.hub.delete.assert_not_called()


def test_heterogeneous_scene_is_preview_only_and_cannot_be_overwritten(monkeypatch):
    raw = raw_scene()
    other = deepcopy(raw['actions'][0])
    other['id'] = other['deviceId'] = 'floor'
    other['attributes']['lightLevel'] = 20
    raw['actions'].append(other)
    provider = native_provider(raw)
    result = provider.get_scene('scene/id')
    assert not result.editable and len(result.targets) == 2
    put = Mock()
    monkeypatch.setattr('app.providers.requests.put', put)
    with pytest.raises(Unsupported):
        provider.update_scene('scene/id', SceneCreate(name='Overwrite', light_ids=['desk']))
    put.assert_not_called()


def test_weekday_scenes_are_not_silently_replaced_by_daily_schedule():
    raw = raw_scene()
    raw['triggers'][1]['trigger']['days'] = ['Mon']
    assert not scene_details(raw).editable


def test_native_transition_round_trip():
    raw = {'id': 'fade', 'type': 'windDownScene', 'info': {'name': 'Sleep'}, 'actions': [],
        'commands': [{'id': 'desk', 'type': 'device', 'enabled': True, 'commands': [{
            'type': 'transitionLight', 'duration': 600,
            'startState': {'isOn': True, 'lightLevel': 80, 'colorTemperature': 4000},
            'endState': {'isOn': False, 'lightLevel': 1, 'colorTemperature': 2200}}]}]}
    decoded = scene_details(raw)
    assert decoded.editable and decoded.kind == 'gradual'
    assert decoded.definition.fade.duration_seconds == 600
    assert decoded.definition.fade.start.temperature == 4000
    assert decoded.definition.state.is_on is False


def test_native_color_control_uses_library_method():
    provider = DirigeraProvider.__new__(DirigeraProvider)
    device = Mock()
    provider._light = Mock(return_value=device)
    provider.normalize = Mock(return_value=MockProvider().list_lights()[0])
    provider.set_color('desk', ColorChange(temperature=2500))
    device.set_color_temperature.assert_called_once_with(2500)
    provider.set_color('desk', ColorChange(hue=240, saturation=.8))
    device.set_light_color.assert_called_once_with(240, .8)
    device.set_light.assert_not_called()
