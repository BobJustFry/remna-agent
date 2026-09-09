"""Тесты разбора подписки. Сеть замокана — проверяем решения, а не связь."""
import base64
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.services import subwatch

LINKS = "\n".join([
    "vless://uuid@1.2.3.4:443?type=xhttp#DE",
    "vless://uuid@1.2.3.5:443?type=raw#FR",
    "hy2://uuid@1.2.3.6:443#RU",
])


class CountConfigsTests(unittest.TestCase):
    def test_plain_list(self) -> None:
        self.assertEqual(subwatch._count_configs(LINKS.encode()), 3)

    def test_base64_payload(self) -> None:
        self.assertEqual(subwatch._count_configs(base64.b64encode(LINKS.encode())), 3)

    def test_base64_with_line_breaks(self) -> None:
        blob = base64.b64encode(LINKS.encode()).decode()
        wrapped = "\n".join(blob[i:i + 40] for i in range(0, len(blob), 40))
        self.assertEqual(subwatch._count_configs(wrapped.encode()), 3)

    def test_error_page_counts_as_nothing(self) -> None:
        page = b"<html><head><title>403 Forbidden</title></head><body>nope</body></html>"
        self.assertEqual(subwatch._count_configs(page), 0)

    def test_empty_body(self) -> None:
        self.assertEqual(subwatch._count_configs(b""), 0)


class ProbeTests(unittest.TestCase):
    def _resp(self, body: bytes, status: int = 200):
        resp = mock.MagicMock()
        resp.status = status
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    def test_healthy_subscription(self) -> None:
        with mock.patch.object(subwatch.urllib.request, "urlopen",
                               return_value=self._resp(LINKS.encode())), \
                mock.patch.object(subwatch, "_cert_days", return_value=61):
            p = subwatch.probe("https://sub.example.com/sub/x", "cdn", 3)
        self.assertTrue(p.ok)
        self.assertEqual(p.hosts, 3)
        self.assertEqual(p.cert_days, 61)

    def test_two_hundred_with_empty_body_is_a_failure(self) -> None:
        """Самый подлый случай: код 200, а подписки внутри нет."""
        with mock.patch.object(subwatch.urllib.request, "urlopen",
                               return_value=self._resp(b"")), \
                mock.patch.object(subwatch, "_cert_days", return_value=61):
            p = subwatch.probe("https://sub.example.com/sub/x", "cdn", 3)
        self.assertFalse(p.ok)
        self.assertEqual(p.hosts, 0)
        self.assertIn("подписка пустая", p.error)

    def test_too_few_configs_is_a_failure(self) -> None:
        one = "vless://uuid@1.2.3.4:443#DE"
        with mock.patch.object(subwatch.urllib.request, "urlopen",
                               return_value=self._resp(one.encode())), \
                mock.patch.object(subwatch, "_cert_days", return_value=61):
            p = subwatch.probe("https://sub.example.com/sub/x", "cdn", 3)
        self.assertFalse(p.ok)

    def test_http_error_is_reported_with_its_code(self) -> None:
        err = subwatch.urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        with mock.patch.object(subwatch.urllib.request, "urlopen", side_effect=err):
            p = subwatch.probe("https://sub.example.com/sub/x", "cdn", 3)
        self.assertFalse(p.ok)
        self.assertEqual(p.http_status, 403)
        self.assertIn("403", p.error)

    def test_missing_url_does_not_reach_the_network(self) -> None:
        with mock.patch.object(subwatch.urllib.request, "urlopen") as opened:
            p = subwatch.probe("  ", "cdn", 3)
        opened.assert_not_called()
        self.assertFalse(p.ok)


class AlertTextTests(unittest.TestCase):
    CFG = {"url": "https://sub.example.com/sub/x"}

    def test_names_the_cdn_when_the_origin_answers(self) -> None:
        main = subwatch.Probe(ok=False, via="cdn", error="подписка ответила 403")
        origin = subwatch.Probe(ok=True, via="origin", hosts=9)
        text = subwatch._alert_text(self.CFG, main, origin)
        self.assertIn("CDN", text)

    def test_names_our_side_when_the_origin_is_silent_too(self) -> None:
        main = subwatch.Probe(ok=False, via="cdn", error="не достучались")
        origin = subwatch.Probe(ok=False, via="origin", error="не достучались")
        text = subwatch._alert_text(self.CFG, main, origin)
        self.assertIn("на нашей стороне", text)

    def test_says_how_long_it_was_down_on_recovery(self) -> None:
        main = subwatch.Probe(ok=True, via="cdn", hosts=9, duration_ms=120)
        text = subwatch._recovery_text(self.CFG, main, timedelta(minutes=42))
        self.assertIn("42", text)


class DeltaSinceTests(unittest.TestCase):
    def test_parses_and_subtracts(self) -> None:
        now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        earlier = (now - timedelta(minutes=5)).isoformat()
        self.assertEqual(subwatch._delta_since(earlier, now), timedelta(minutes=5))

    def test_garbage_gives_nothing(self) -> None:
        now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        self.assertIsNone(subwatch._delta_since(None, now))
        self.assertIsNone(subwatch._delta_since("не дата", now))


if __name__ == "__main__":
    unittest.main()
