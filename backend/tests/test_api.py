import pytest
from fastapi.testclient import TestClient
from requests.exceptions import ConnectionError
from app.main import create_app
from app.models import SceneCreate
from app.providers import MockProvider, NotFound


@pytest.fixture
def provider():
    return MockProvider()


@pytest.fixture
def client(provider):
    with TestClient(create_app(provider)) as client:
        yield client


def test_discovery(client):
    lights = client.get('/api/lights').json()
    assert len(lights) == 3
    assert lights[0]['brightness'] == 70
    assert lights[0]['reachable'] is True
    assert client.get('/api/status').json()['provider'] == 'mock'


def test_power_and_brightness(client):
    assert client.put('/api/lights/desk/power', json={'is_on': False}).status_code == 204
    assert client.put('/api/lights/desk/brightness', json={'brightness': 42}).status_code == 204
    light = client.get('/api/lights').json()[0]
    assert light['is_on'] is False
    assert light['brightness'] == 42


@pytest.mark.parametrize('value', [0, 101, -1, '30', 1.2, True])
def test_brightness_validation(client, value):
    assert client.put('/api/lights/desk/brightness', json={'brightness': value}).status_code == 422


def test_room_grouping_and_controls(client):
    rooms = client.get('/api/rooms').json()
    assert rooms[0]['light_ids'] == ['desk', 'floor']
    assert client.put('/api/rooms/study/power', json={'is_on': False}).status_code == 204
    assert client.put('/api/rooms/study/brightness', json={'brightness': 10}).status_code == 204
    lights = client.get('/api/lights').json()
    assert all(not light['is_on'] and light['brightness'] == 10 for light in lights[:2])
    assert lights[2]['brightness'] == 25
    assert lights[2]['is_on'] is True


def test_missing_and_invalid(client):
    assert client.put('/api/lights/missing/power', json={'is_on': True}).status_code == 404
    assert client.put('/api/rooms/missing/power', json={'is_on': True}).status_code == 404
    assert client.post('/api/scenes/missing/activate').status_code == 404
    assert client.put('/api/lights/desk/power', json={'is_on': 'yes'}).status_code == 422
    assert client.post('/api/scenes', json={'name': ' ', 'light_ids': ['desk']}).status_code == 422


def test_scenes(client):
    assert client.get('/api/scenes').json()[0]['name'] == 'Evening'
    assert client.post('/api/scenes/evening/activate').status_code == 204
    assert client.get('/api/lights').json()[0]['brightness'] == 30
    saved = client.post('/api/scenes', json={'name': 'Saved', 'light_ids': ['desk']})
    assert saved.status_code == 201
    client.put('/api/lights/desk/power', json={'is_on': False})
    assert client.post(f"/api/scenes/{saved.json()['id']}/activate").status_code == 204
    assert client.get('/api/lights').json()[0]['is_on'] is True


def test_provider_copies(provider):
    provider.list_lights()[0].name = 'changed'
    assert provider.list_lights()[0].name == 'Desk light'
    with pytest.raises(NotFound):
        provider.create_scene(SceneCreate(name='Missing', light_ids=['absent']))


def test_unavailable_sanitizes_errors(provider):
    def fail():
        raise ConnectionError('private-token-never-return')
    provider.list_lights = fail
    with TestClient(create_app(provider)) as client:
        response = client.get('/api/lights')
        assert response.status_code == 503
        assert 'private-token' not in response.text


def test_room_partial_failure(client, provider):
    original = provider.set_power
    def fail_second(id_, value):
        if id_ == 'floor':
            raise ConnectionError('offline')
        original(id_, value)
    provider.set_power = fail_second
    response = client.put('/api/rooms/study/power', json={'is_on': False})
    assert response.status_code == 503
    assert 'some lights may already have changed' in response.json()['detail']


def test_cors(client):
    response = client.options('/api/lights/desk/power', headers={'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'PUT'})
    assert response.headers['access-control-allow-origin'] == 'http://localhost:5173'
