"""Small in-process event bus for authenticated browser WebSocket sessions.

Events are notifications, not a source of truth. Receivers re-read authorized
server state, and slow clients have bounded queues. The status stream also
samples backend diagnostics to detect out-of-process failures.
"""
from __future__ import annotations

import asyncio
import threading


class PortalRealtimeHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._listeners: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        with self._lock:
            self._listeners.add((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._listeners = {listener for listener in self._listeners if listener[1] is not queue}

    @staticmethod
    def enqueue(queue: asyncio.Queue, message: dict) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(message)

    def publish(self, path: str) -> None:
        # The HTTP middleware and the adapter monitor thread can both publish.
        if not isinstance(path, str) or not path.startswith("/"):
            return
        with self._lock:
            listeners = tuple(self._listeners)
        for loop, queue in listeners:
            try:
                loop.call_soon_threadsafe(self.enqueue, queue, {"type": "resource-changed", "path": path})
            except RuntimeError:
                # The event loop is stopping; its socket will be disconnected.
                pass


portal_realtime_hub = PortalRealtimeHub()
