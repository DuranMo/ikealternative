import asyncio
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from requests.exceptions import ConnectionError, Timeout, HTTPError
from requests import Response
from app.main import create_app
from app.providers import MockProvider, Unsupported
from app.history import CommandHistory
from app.sync import StateSync


def test_history_success_persistence_and_latency(tmp_path, monkeypatch):
    path = tmp_path / 'history.sqlite3'
    values = iter([10, 10.125])
    monkeypatch.setattr('app.history.perf_counter', lambda: next(values))
    with TestClient(create_app(MockProvider(), db_path=path)) as client:
        assert client.put('/api/lights/desk/power', json={'is_on': False}).status_code == 204
        record = client.get('/api/commands').json()[0]
        assert record['target_type'] == 'light'
        assert record['target_id'] == 'desk'
        assert record['command_type'] == 'set_power'
        assert record['success'] is True
        assert record['error_category'] is None
        assert record['latency_ms'] == 125
        assert datetime.fromisoformat(record['timestamp']).tzinfo is not None
        assert client.get('/api/metrics').json()['successful'] == 1
    with TestClient(create_app(MockProvider(), db_path=path)) as client:
        assert client.get('/api/commands').json() == [record]
        assert client.get('/api/commands?limit=0').status_code == 422
        assert client.get('/api/commands?limit=501').status_code == 422


@pytest.mark.parametrize('exc,expected,status', [
    (ConnectionError('secret-token'), 'connectivity', 503),
    (Timeout('secret-token'), 'timeout', 503),
    (Unsupported('secret-token'), 'unsupported', 409),
    (RuntimeError('secret-token'), 'command', 502),
])
def test_command_failures_are_logged_and_sanitized(exc, expected, status):
    provider = MockProvider()
    def fail(*args):
        raise exc
    provider.set_power = fail
    with TestClient(create_app(provider)) as client:
        response = client.put('/api/lights/desk/power', json={'is_on': False})
        assert response.status_code == status
        assert 'secret-token' not in response.text
        records = client.get('/api/commands').json()
        assert len(records) == 1
        assert records[0]['success'] is False
        assert records[0]['error_category'] == expected
        assert 'secret-token' not in str(records)
        assert client.get('/api/lights').json()[0]['is_on'] is True
        assert client.get('/api/metrics').json()['failed'] == 1


def test_room_and_scene_logging_and_validation():
    with TestClient(create_app(MockProvider())) as client:
        client.put('/api/rooms/study/brightness', json={'brightness': 35})
        client.post('/api/scenes/evening/activate')
        client.post('/api/scenes', json={'name': 'Private scene name', 'light_ids': ['desk']})
        client.put('/api/lights/missing/power', json={'is_on': False})
        client.put('/api/lights/desk/brightness', json={'brightness': 101})
        records = client.get('/api/commands').json()
        assert len(records) == 4  # Validation failures never attempt provider execution.
        assert records[0]['error_category'] == 'not_found'
        assert records[-1]['target_type'] == 'room'
        assert records[-1]['target_id'] == 'study'
        assert records[-1]['command_type'] == 'set_brightness'
        assert 'Private scene name' not in str(records)


def test_room_failure_retains_connectivity_category():
    provider = MockProvider()
    original = provider.set_power
    def partial(id_, value):
        if id_ == 'floor':
            raise Timeout('sensitive')
        original(id_, value)
    provider.set_power = partial
    with TestClient(create_app(provider)) as client:
        response = client.put('/api/rooms/study/power', json={'is_on': False})
        assert response.status_code == 503
        assert 'some lights' in response.text
        record = client.get('/api/commands').json()[0]
        assert record['target_type'] == 'room'
        assert record['error_category'] == 'timeout'
        assert record['success'] is False


def test_http_rejection_is_command_error():
    provider = MockProvider()
    response = Response()
    response.status_code = 400
    def fail(*args):
        raise HTTPError('private', response=response)
    provider.set_power = fail
    with TestClient(create_app(provider)) as client:
        assert client.put('/api/lights/desk/power', json={'is_on': False}).status_code == 502
        assert client.get('/api/commands').json()[0]['error_category'] == 'command'


def test_history_storage_failure_does_not_change_command_outcome():
    app = create_app(MockProvider())
    with TestClient(app) as client:
        app.state.history.close()
        assert client.put('/api/lights/desk/power', json={'is_on': False}).status_code == 204
        assert client.get('/api/status').json()['history_available'] is False
        assert client.get('/api/commands').status_code == 503
        assert client.get('/api/lights').json()[0]['is_on'] is False


def test_state_change_detection_recovery_and_bounded_queues():
    async def scenario():
        provider = MockProvider()
        fail = False
        def read():
            if fail:
                raise Timeout('never broadcast this')
            return list(reversed(provider.list_lights()))
        sync = StateSync(read)
        assert await sync.poll_once() is True
        queue = sync.subscribe()
        first = queue.get_nowait()
        assert first['available'] is True
        assert await sync.poll_once() is False
        assert queue.empty()
        provider.set_power('desk', False)  # Simulated external change.
        assert await sync.poll_once() is True
        provider.set_brightness('desk', 5)
        assert await sync.poll_once() is True
        assert queue.qsize() == 1
        latest = queue.get_nowait()
        assert latest['revision'] == 3
        assert next(light for light in latest['lights'] if light['id'] == 'desk')['brightness'] == 5
        fail = True
        assert await sync.poll_once() is True
        offline = queue.get_nowait()
        assert offline['lights'] == latest['lights']
        assert offline['available'] is False
        assert offline['error_category'] == 'timeout'
        assert 'never broadcast' not in str(offline)
        assert await sync.poll_once() is False
        fail = False
        assert await sync.poll_once() is True
        assert queue.get_nowait()['available'] is True
    asyncio.run(scenario())


def test_slow_hub_read_does_not_block_event_loop():
    import threading
    async def scenario():
        started = threading.Event()
        release = threading.Event()
        def read():
            started.set()
            release.wait(2)
            return MockProvider().list_lights()
        sync = StateSync(read)
        task = asyncio.create_task(sync.poll_once())
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(.001)
            assert started.is_set()
            assert not task.done()
            await asyncio.sleep(.01)  # Event loop stays responsive while the provider blocks.
        finally:
            release.set()
            await task
    asyncio.run(scenario())


def test_two_websocket_clients_initial_updates_and_cleanup():
    app = create_app(MockProvider(), poll_interval=3600)
    with TestClient(app) as client:
        with client.websocket_connect('/api/ws') as first, client.websocket_connect('/api/ws') as second:
            initial = first.receive_json()
            assert initial == second.receive_json()
            assert len(initial['lights']) == 3
            assert client.put('/api/lights/desk/power', json={'is_on': False}).status_code == 204
            changed = first.receive_json()
            assert changed == second.receive_json()
            assert changed['revision'] > initial['revision']
            assert next(light for light in changed['lights'] if light['id'] == 'desk')['is_on'] is False
        with client.websocket_connect('/api/ws') as reconnected:
            assert reconnected.receive_json()['lights'] == changed['lights']
    assert app.state.sync.task.done()
    assert not app.state.sync.clients


def test_external_change_detected_by_background_poll():
    provider = MockProvider()
    with TestClient(create_app(provider, poll_interval=.02)) as client:
        with client.websocket_connect('/api/ws') as ws:
            initial = ws.receive_json()
            provider.set_brightness('desk', 17)
            changed = ws.receive_json()
            assert changed['revision'] > initial['revision']
            assert next(light for light in changed['lights'] if light['id'] == 'desk')['brightness'] == 17


def test_websocket_origin_rejected():
    from starlette.websockets import WebSocketDisconnect
    with TestClient(create_app(MockProvider())) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/api/ws', headers={'origin': 'https://untrusted.example'}):
                pass


def test_already_closed_socket_cleanup(monkeypatch):
    from starlette.websockets import WebSocket, WebSocketDisconnect
    async def already_closed(self, *args, **kwargs):
        raise WebSocketDisconnect(code=1006)
    monkeypatch.setattr(WebSocket, 'close', already_closed)
    app = create_app(MockProvider())
    with TestClient(app) as client:
        with client.websocket_connect('/api/ws') as ws:
            assert ws.receive_json()['available'] is True
    assert not app.state.sync.clients


def test_configuration_failure_is_logged_without_reading_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr('app.main.load_dotenv', lambda *args: None)
    monkeypatch.setenv('SMART_HOME_PROVIDER', 'invalid')
    with TestClient(create_app(db_path=tmp_path / 'config.sqlite3')) as client:
        with client.websocket_connect('/api/ws') as ws:
            assert ws.receive_json()['available'] is False
        assert client.put('/api/lights/desk/power', json={'is_on': True}).status_code == 503
        records = client.get('/api/commands').json()
        assert records[0]['error_category'] == 'connectivity'
        assert records[0]['success'] is False
