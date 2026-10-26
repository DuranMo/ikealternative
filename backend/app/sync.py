"""One thread-offloaded hub poll, many bounded client queues, no device writes."""
import asyncio
from datetime import datetime, timezone
from .errors import category


class StateSync:
    def __init__(self, read_lights, interval=3):
        self.read_lights = read_lights
        self.interval = interval
        self.clients = set()
        self.snapshot = None
        self.revision = 0
        self.sample_lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.stop = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.task = None

    async def poll_once(self):
        async with self.sample_lock:
            try:
                lights = await asyncio.to_thread(self.read_lights)
                state = {'lights': [light.model_dump(mode='json') for light in sorted(lights, key=lambda light: light.id)],
                         'available': True, 'error_category': None}
            except Exception as exc:
                state = {'lights': self.snapshot['lights'] if self.snapshot else [],
                         'available': False, 'error_category': category(exc)}
            relevant = {key: self.snapshot[key] for key in state} if self.snapshot else None
            if state == relevant:
                return False
            self.revision += 1
            self.snapshot = {'type': 'state', **state, 'revision': self.revision,
                             'updated_at': datetime.now(timezone.utc).isoformat()}
            for queue in tuple(self.clients):
                self._offer(queue, self.snapshot)
            return True

    @staticmethod
    def _offer(queue, snapshot):
        # A slow browser needs the latest complete snapshot, not an unbounded backlog.
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(snapshot)

    def subscribe(self):
        queue = asyncio.Queue(maxsize=1)
        self.clients.add(queue)
        if self.snapshot is not None:
            self._offer(queue, self.snapshot)
        return queue

    def request_refresh(self):
        self.loop.call_soon_threadsafe(self.wake.set)

    async def run(self):
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=self.interval)
            except TimeoutError:
                pass
            self.wake.clear()
            if not self.stop.is_set():
                await self.poll_once()

    async def start(self):
        await self.poll_once()
        self.task = asyncio.create_task(self.run(), name='hub-state-poller')

    async def close(self):
        self.stop.set()
        self.wake.set()
        if self.task:
            await self.task
