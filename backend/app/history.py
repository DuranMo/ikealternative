"""Application metrics only. Never persists hub state, scene definitions, or errors."""
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from time import perf_counter
from .errors import category


class CommandHistory:
    def __init__(self, path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.healthy = True
        with self.db:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('''CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                command_type TEXT NOT NULL,
                latency_ms REAL NOT NULL,
                success INTEGER NOT NULL,
                error_category TEXT
            )''')

    def execute(self, target_type, target_id, command_type, action):
        timestamp = datetime.now(timezone.utc).isoformat()
        started = perf_counter()
        success, error = False, None
        try:
            result = action()
            success = True
            return result
        except Exception as exc:
            error = category(exc)
            raise
        finally:
            latency = (perf_counter() - started) * 1000
            try:
                with self.lock, self.db:
                    self.db.execute('''INSERT INTO commands
                        (timestamp,target_type,target_id,command_type,latency_ms,success,error_category)
                        VALUES (?,?,?,?,?,?,?)''',
                        (timestamp, target_type, target_id, command_type, latency, int(success), error))
                self.healthy = True
            except sqlite3.Error:
                # A telemetry failure must not misreport or retry a successful device command.
                self.healthy = False
                logging.getLogger(__name__).error('Command history write failed; command outcome preserved.')

    def recent(self, limit=50):
        with self.lock:
            rows = self.db.execute('SELECT * FROM commands ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return [{**dict(row), 'success': bool(row['success'])} for row in rows]

    def summary(self):
        with self.lock:
            row = self.db.execute('''SELECT COUNT(*) AS total, COALESCE(SUM(success),0) AS successful,
                AVG(latency_ms) AS average_latency_ms FROM commands''').fetchone()
        result = dict(row)
        result['failed'] = result['total'] - result['successful']
        result['storage_healthy'] = self.healthy
        return result

    def close(self):
        with self.lock:
            self.db.close()
