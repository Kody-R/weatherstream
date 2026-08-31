from __future__ import annotations

import unittest

from app.observability import Observability


class ObservabilityTests(unittest.TestCase):
    def test_metrics_and_bounded_events(self) -> None:
        metrics = Observability(event_limit=50)
        metrics.count_request("GET", "/health/live", 200, 0.01)
        metrics.increment("cache_cleanups_total")
        for index in range(60):
            metrics.event("test", f"event {index}")
        output = metrics.prometheus()
        self.assertIn("weatherstream_http_requests_total 1", output)
        self.assertIn('route="/health/live"', output)
        self.assertEqual(len(metrics.events(300)), 50)
        self.assertEqual(metrics.events(1)[0]["message"], "event 59")

    def test_subscribers_receive_a_copy_and_can_unsubscribe(self) -> None:
        metrics = Observability()
        received = []
        def subscriber(event):
            received.append(event)
            event["message"] = "mutated"
        metrics.subscribe(subscriber)
        metrics.event("source", "recovered", source="nws_alerts")
        metrics.unsubscribe(subscriber)
        metrics.event("source", "second")
        self.assertEqual(len(received), 1)
        self.assertEqual(metrics.events(2)[0]["message"], "recovered")


if __name__ == "__main__":
    unittest.main()
