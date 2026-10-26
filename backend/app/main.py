import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
from anyio import CancelScope
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from .models import Light, Room, Scene, Power, Brightness, SceneCreate, SceneDetails, ColorChange
from .providers import DirigeraProvider, MockProvider, Unavailable
from .service import SmartHomeService
from .history import CommandHistory
from .sync import StateSync
from .errors import public_error

ORIGINS = ['http://localhost:5173', 'http://127.0.0.1:5173',
           'http://localhost:8000', 'http://127.0.0.1:8000']


def create_app(provider=None, db_path=None, poll_interval=3):
    if poll_interval <= 0:
        raise ValueError('poll_interval must be positive')
    config_lock = RLock()

    def service():
        with config_lock:
            if not hasattr(app.state, 'service'):
                selected = provider
                if selected is None:
                    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
                    mode = os.getenv('SMART_HOME_PROVIDER', 'dirigera')
                    if mode == 'mock':
                        selected = MockProvider()
                    elif mode == 'dirigera':
                        ip, token = os.getenv('DIRIGERA_IP'), os.getenv('DIRIGERA_TOKEN')
                        if not ip or not token:
                            raise Unavailable('Hub credentials are missing.')
                        selected = DirigeraProvider(ip, token)
                    else:
                        raise Unavailable('Invalid provider configuration.')
                app.state.service = SmartHomeService(selected)
            return app.state.service

    def invoke(operation, *args):
        svc = service()
        with svc.lock:
            if operation in ('rooms', 'room_control', 'set_all_color'):
                return getattr(svc, operation)(*args)
            return getattr(svc.provider, operation)(*args)

    @asynccontextmanager
    async def lifespan(app):
        # Tests with injected providers default to isolated in-memory telemetry.
        path = db_path if db_path is not None else (
            ':memory:' if provider is not None else os.getenv(
                'IKE_DATABASE_PATH', str(Path(__file__).resolve().parents[1] / 'data' / 'commands.sqlite3')))
        history = await asyncio.to_thread(CommandHistory, path)
        app.state.history = history
        sync = StateSync(lambda: invoke('list_lights'), interval=poll_interval)
        app.state.sync = sync
        try:
            await sync.start()
            yield
        finally:
            await sync.close()
            await asyncio.to_thread(history.close)

    app = FastAPI(title='IKEAlternative', version='0.2.0', lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=ORIGINS[:2],
                       allow_methods=['GET', 'PUT', 'POST', 'DELETE'], allow_headers=['Content-Type'])

    def call(operation, *args):
        try:
            return invoke(operation, *args)
        except Exception as exc:
            status, message = public_error(exc)
            raise HTTPException(status, message) from None

    def command(target_type, target_id, operation, *args):
        # Measures dispatch/provider work, excluding the SQLite insert and later readback.
        try:
            return app.state.history.execute(target_type, target_id,
                args[1] if operation == 'room_control' else operation,
                lambda: invoke(operation, *args))
        except Exception as exc:
            status, message = public_error(exc)
            raise HTTPException(status, message) from None
        finally:
            # Failed room commands can partially succeed; always read back source-of-truth state.
            app.state.sync.request_refresh()

    @app.get('/api/status')
    def status():
        try:
            selected = service().provider
        except Exception as exc:
            code, message = public_error(exc)
            raise HTTPException(code, message) from None
        return {'provider': 'mock' if isinstance(selected, MockProvider) else 'dirigera',
                'hub_available': app.state.sync.snapshot['available'],
                'history_available': app.state.history.healthy}

    @app.get('/api/lights', response_model=list[Light])
    def lights():
        return call('list_lights')

    @app.put('/api/lights/color', status_code=204)
    def all_color(body: ColorChange):
        command('all_lights', 'all', 'set_all_color', body)

    @app.put('/api/lights/{light_id}/power', status_code=204)
    def power(light_id: str, body: Power):
        command('light', light_id, 'set_power', light_id, body.is_on)

    @app.put('/api/lights/{light_id}/brightness', status_code=204)
    def brightness(light_id: str, body: Brightness):
        command('light', light_id, 'set_brightness', light_id, body.brightness)

    @app.get('/api/rooms', response_model=list[Room])
    def rooms():
        return call('rooms')

    @app.put('/api/rooms/{room_id}/power', status_code=204)
    def room_power(room_id: str, body: Power):
        command('room', room_id, 'room_control', room_id, 'set_power', body.is_on)

    @app.put('/api/rooms/{room_id}/brightness', status_code=204)
    def room_brightness(room_id: str, body: Brightness):
        command('room', room_id, 'room_control', room_id, 'set_brightness', body.brightness)

    @app.get('/api/scenes', response_model=list[Scene])
    def scenes():
        return call('list_scenes')

    @app.get('/api/scenes/{scene_id}', response_model=SceneDetails)
    def scene_detail(scene_id: str):
        return call('get_scene', scene_id)

    @app.put('/api/scenes/{scene_id}', response_model=Scene)
    def update_scene(scene_id: str, body: SceneCreate):
        return command('scene', scene_id, 'update_scene', scene_id, body)

    @app.delete('/api/scenes/{scene_id}', status_code=204)
    def delete_scene(scene_id: str):
        command('scene', scene_id, 'delete_scene', scene_id)

    @app.post('/api/scenes/{scene_id}/activate', status_code=204)
    def activate(scene_id: str):
        command('scene', scene_id, 'activate_scene', scene_id)

    @app.post('/api/scenes', response_model=Scene, status_code=201)
    def create(body: SceneCreate):
        return command('scene', 'new', 'create_scene', body)

    @app.get('/api/commands')
    def commands(limit: int = Query(default=50, ge=1, le=500)):
        try:
            return app.state.history.recent(limit)
        except sqlite3.Error:
            raise HTTPException(503, 'Command history is unavailable.') from None

    @app.get('/api/metrics')
    def metrics():
        try:
            return app.state.history.summary()
        except sqlite3.Error:
            raise HTTPException(503, 'Command history is unavailable.') from None

    @app.websocket('/api/ws')
    async def websocket(socket: WebSocket):
        origin = socket.headers.get('origin')
        if origin is not None and origin not in ORIGINS:
            await socket.close(code=1008)
            return
        await socket.accept()
        sync = app.state.sync
        queue = sync.subscribe()

        async def send():
            while True:
                snapshot = await queue.get()
                await asyncio.wait_for(socket.send_json(snapshot), timeout=5)

        async def receive():
            while True:
                await socket.receive_text()

        tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, TimeoutError, RuntimeError, OSError, asyncio.CancelledError):
            pass
        finally:
            sync.clients.discard(queue)
            for task in tasks:
                task.cancel()
            with CancelScope(shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await socket.close()
                except (WebSocketDisconnect, RuntimeError, OSError):
                    pass

    return app


app = create_app()
