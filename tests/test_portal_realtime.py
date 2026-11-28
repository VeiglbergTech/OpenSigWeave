"""Realtime event bus, WebSocket scope and no-browser-poll regression tests."""
import asyncio
import threading
import unittest

from core.portal_realtime import PortalRealtimeHub


class PortalRealtimeTests(unittest.TestCase):
    def test_change_events_delivered_from_another_thread_and_unsubscribe(self):
        async def exercise():
            hub = PortalRealtimeHub()
            subscription = hub.subscribe()
            worker = threading.Thread(target=lambda: hub.publish("/api/admin/signatures"), daemon=True)
            worker.start()
            worker.join(timeout=2)
            event = await asyncio.wait_for(subscription.get(), timeout=1)
            self.assertEqual(event, {
                "type": "resource-changed", "path": "/api/admin/signatures"
            })
            hub.unsubscribe(subscription)
            hub.publish("/api/admin/signature-groups")
            await asyncio.sleep(0)
            self.assertTrue(subscription.empty())
        asyncio.run(exercise())

    def test_pending_events_are_bounded(self):
        async def exercise():
            hub = PortalRealtimeHub()
            subscription = hub.subscribe()
            for i in range(60):
                hub.enqueue(subscription, {"type": "resource-changed", "path": f"/api/admin/{i}"})
            self.assertEqual(subscription.qsize(), 32)
            self.assertEqual(subscription.get_nowait()["path"], "/api/admin/28")
            hub.unsubscribe(subscription)
        asyncio.run(exercise())

    def test_invalid_non_absolute_change_is_not_broadcast(self):
        async def exercise():
            hub = PortalRealtimeHub()
            subscription = hub.subscribe()
            hub.publish("api/admin/signatures")
            await asyncio.sleep(0)
            self.assertTrue(subscription.empty())
            hub.unsubscribe(subscription)
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
