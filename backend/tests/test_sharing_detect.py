"""Тесты сбора признаков шаринга. Панель и DNS не трогаем — подменяем."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.services import sharing_detect as sd
from app.services import sharing_watch

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def rows(*pairs: tuple[str, int]) -> list[dict]:
    """(адрес, через сколько секунд после T0 его видели в последний раз)."""
    return [{"ip": ip, "lastSeen": (T0 + timedelta(seconds=sec)).isoformat()}
            for ip, sec in pairs]


class ConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        # Один адрес — один оператор, страна по первому октету: так проверяется
        # сама логика окна, а не резолвер.
        patch_net = mock.patch.object(sd.asn_lookup, "networks_of",
                                      lambda ips: {"as:" + ip.split(".")[0] for ip in ips})
        patch_cc = mock.patch.object(sd.asn_lookup, "countries_of",
                                     lambda ips: {"RU" if ip.startswith("5") else "DE"
                                                  for ip in ips})
        patch_net.start()
        patch_cc.start()
        self.addCleanup(patch_net.stop)
        self.addCleanup(patch_cc.stop)

    def test_addresses_one_after_another_are_not_concurrent(self) -> None:
        # Человек переезжает с вышки на вышку: адреса идут через пять минут.
        ips, nets, _ = sd._concurrency(rows(("5.1.1.1", 0), ("6.2.2.2", 300),
                                            ("7.3.3.3", 600)))
        self.assertEqual(ips, 1)
        self.assertEqual(nets, 1)

    def test_a_handful_in_one_minute_is_concurrent(self) -> None:
        ips, nets, _ = sd._concurrency(rows(("5.1.1.1", 0), ("6.2.2.2", 10),
                                            ("7.3.3.3", 20), ("8.4.4.4", 30)))
        self.assertEqual(ips, 4)
        self.assertEqual(nets, 4)

    def test_countries_come_from_the_widest_group(self) -> None:
        _, _, countries = sd._concurrency(rows(("5.1.1.1", 0), ("9.2.2.2", 10)))
        self.assertEqual(countries, ["DE", "RU"])

    def test_no_timestamps_means_nothing_to_count(self) -> None:
        self.assertEqual(sd._concurrency([{"ip": "5.1.1.1", "lastSeen": None}]), (0, 0, []))


class SweepTests(unittest.TestCase):
    def test_rotation_walks_the_whole_park(self) -> None:
        sd._sweep["at"] = 0
        items = list(range(10))
        seen: set[int] = set()
        for _ in range(4):
            seen.update(sd._rotate(items, 3))
        self.assertEqual(len(seen), 10)

    def test_rotation_wraps_around_the_end(self) -> None:
        sd._sweep["at"] = 8
        self.assertEqual(sd._rotate(list(range(10)), 4), [8, 9, 0, 1])

    def test_nothing_to_take(self) -> None:
        self.assertEqual(sd._rotate([], 5), [])
        self.assertEqual(sd._rotate([1, 2], 0), [])


class HwidTests(unittest.TestCase):
    CFG = {"hwid_active_min": 60}

    @staticmethod
    def dev(platform: str, minutes_ago: int) -> dict:
        return {"platform": platform, "hwid": platform + str(minutes_ago),
                "updatedAt": (T0 - timedelta(minutes=minutes_ago)).isoformat()}

    def call(self, devices: list[dict]):
        with mock.patch.object(sd, "_try_api", return_value={"devices": devices}):
            return sd.hwid_features(1, self.CFG, T0)

    def test_reinstalls_do_not_count_as_devices(self) -> None:
        # Старое устройство перестало обновляться месяц назад — это переустановка.
        platforms, active, total = self.call([self.dev("Android", 2),
                                              self.dev("Android", 43200)])
        self.assertEqual(active, 1)
        self.assertEqual(total, 2)
        self.assertEqual(platforms, ["Android"])

    def test_three_live_platforms_are_seen(self) -> None:
        platforms, active, _ = self.call([self.dev("iOS", 1), self.dev("Android", 5),
                                          self.dev("Windows", 30)])
        self.assertEqual(active, 3)
        self.assertEqual(platforms, ["Android", "Windows", "iOS"])

    def test_panel_error_is_not_a_signal(self) -> None:
        with mock.patch.object(sd, "_try_api", return_value={"_error": "500"}):
            self.assertEqual(sd.hwid_features(1, self.CFG, T0), ([], 0, 0))


class AlertTextTests(unittest.TestCase):
    HIT = {"user_id": 42, "username": "someone", "score": 71, "device_limit": 1,
           "unlimited": False, "rw_nodes": ["de-1"],
           "parts": [{"key": "conc_ips", "points": 24.0, "why": "одновременно 9 адресов"}]}

    def test_alert_names_the_person_and_the_reason(self) -> None:
        text = sharing_watch.alert_text("alert", self.HIT)
        self.assertIn("someone", text)
        self.assertIn("71", text)
        self.assertIn("одновременно 9 адресов", text)
        # Лимит устройств — платная услуга владельца, рагент его не трогает.
        self.assertIn("не трогаем", text)

    def test_clear_says_it_is_over(self) -> None:
        self.assertIn("снят", sharing_watch.alert_text("clear", self.HIT))

    def test_html_in_a_username_cannot_break_the_message(self) -> None:
        text = sharing_watch.alert_text("alert", {**self.HIT, "username": "<b>x</b>"})
        self.assertNotIn("<b>x</b>", text)
        self.assertIn("&lt;b&gt;", text)


if __name__ == "__main__":
    unittest.main()
