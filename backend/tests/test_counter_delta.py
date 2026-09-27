import importlib.util
import unittest
from pathlib import Path

from app.services.counter_delta import (
    CounterSnapshot,
    CounterTracker,
    counter_deltas,
    retr_pct,
)


def snap(boot="b1", out=1000, retr=10, oom=0) -> CounterSnapshot:
    return CounterSnapshot(boot_id=boot, tcp_out=out, tcp_retrans=retr, oom_kill=oom)


class CounterDeltaTests(unittest.TestCase):
    def test_first_tick_is_empty(self) -> None:
        d = counter_deltas(None, snap())
        self.assertEqual(d, {"tcp_out_segs": None, "tcp_retrans_segs": None, "oom_kills": None})

    def test_plain_delta(self) -> None:
        d = counter_deltas(snap(out=1000, retr=10, oom=1), snap(out=5000, retr=50, oom=3))
        self.assertEqual(d, {"tcp_out_segs": 4000, "tcp_retrans_segs": 40, "oom_kills": 2})

    def test_reboot_by_boot_id(self) -> None:
        # Новая загрузка: даже если счётчики успели перерасти старые — не верим.
        d = counter_deltas(snap(boot="b1", out=100), snap(boot="b2", out=900, retr=20, oom=1))
        self.assertIsNone(d["tcp_out_segs"])
        self.assertIsNone(d["tcp_retrans_segs"])
        self.assertIsNone(d["oom_kills"])

    def test_negative_delta_ignored(self) -> None:
        # Счётчики сбросились без boot_id (агент старый или не прочитал файл).
        d = counter_deltas(
            snap(boot=None, out=9000, retr=90, oom=5), snap(boot=None, out=100, retr=1, oom=0)
        )
        self.assertIsNone(d["tcp_out_segs"])
        self.assertIsNone(d["tcp_retrans_segs"])
        self.assertIsNone(d["oom_kills"])

    def test_oom_independent_of_tcp(self) -> None:
        d = counter_deltas(snap(out=None, retr=None, oom=2), snap(out=None, retr=None, oom=4))
        self.assertIsNone(d["tcp_out_segs"])
        self.assertEqual(d["oom_kills"], 2)

    def test_stale_status_gives_no_tcp(self) -> None:
        # Тот же отсчёт второй раз (опрос отдал кеш) — ноль отправленных не пишем.
        d = counter_deltas(snap(), snap())
        self.assertIsNone(d["tcp_out_segs"])
        self.assertIsNone(d["tcp_retrans_segs"])
        self.assertEqual(d["oom_kills"], 0)

    def test_retrans_above_out_rejected(self) -> None:
        d = counter_deltas(snap(out=1000, retr=10), snap(out=1010, retr=100))
        self.assertIsNone(d["tcp_out_segs"])
        self.assertIsNone(d["tcp_retrans_segs"])


class CounterTrackerTests(unittest.TestCase):
    def test_gap_keeps_previous(self) -> None:
        t = CounterTracker()
        t.update("n", snap(out=1000, retr=10))
        # Агент пропал на тик — прошлый отсчёт не теряем.
        self.assertIsNone(t.update("n", None)["tcp_out_segs"])
        self.assertIsNone(t.update("n", snap(out=None, retr=None, oom=None))["tcp_out_segs"])
        d = t.update("n", snap(out=3000, retr=30))
        self.assertEqual(d["tcp_out_segs"], 2000)
        self.assertEqual(d["tcp_retrans_segs"], 20)

    def test_nodes_are_separate(self) -> None:
        t = CounterTracker()
        t.update("a", snap(out=100))
        self.assertIsNone(t.update("b", snap(out=500))["tcp_out_segs"])
        self.assertEqual(t.update("a", snap(out=300))["tcp_out_segs"], 200)

    def test_after_reboot_next_tick_counts(self) -> None:
        t = CounterTracker()
        t.update("n", snap(boot="b1", out=90000, retr=900))
        self.assertIsNone(t.update("n", snap(boot="b2", out=100, retr=1))["tcp_out_segs"])
        self.assertEqual(t.update("n", snap(boot="b2", out=600, retr=6))["tcp_out_segs"], 500)


class RetrPctTests(unittest.TestCase):
    def test_ratio(self) -> None:
        self.assertEqual(retr_pct(40, 4000), 1.0)
        self.assertEqual(retr_pct(1, 3), 33.33)

    def test_sum_based_not_mean_of_percents(self) -> None:
        # Шаг из двух отсчётов: 10/100 (10%) и 0/9900 (0%). Среднее процентов
        # дало бы 5%, а по суммам — 0.1%.
        self.assertEqual(retr_pct(10 + 0, 100 + 9900), 0.1)

    def test_empty_or_zero(self) -> None:
        self.assertIsNone(retr_pct(None, 100))
        self.assertIsNone(retr_pct(5, None))
        self.assertIsNone(retr_pct(0, 0))
        self.assertEqual(retr_pct(0, 100), 0.0)

    def test_decimal_from_postgres_sum(self) -> None:
        from decimal import Decimal

        self.assertEqual(retr_pct(Decimal(25), Decimal(1000)), 2.5)


def _load_agent():
    path = Path(__file__).resolve().parents[2] / "agent" / "remna_node_agent.py"
    spec = importlib.util.spec_from_file_location("remna_node_agent", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class AgentRuPingTests(unittest.TestCase):
    agent = _load_agent()

    def test_parse_iputils(self) -> None:
        out = (
            "--- 185.32.187.161 ping statistics ---\n"
            "5 packets transmitted, 4 received, 20% packet loss, time 804ms\n"
            "rtt min/avg/max/mdev = 40.112/41.250/43.001/1.100 ms\n"
        )
        m = self.agent._PING_SENT_RE.search(out)
        self.assertEqual((m.group(1), m.group(2)), ("5", "4"))
        self.assertEqual(self.agent._PING_RTT_RE.search(out).group(1), "41.250")

    def test_parse_busybox(self) -> None:
        out = (
            "5 packets transmitted, 5 packets received, 0% packet loss\n"
            "round-trip min/avg/max = 1.1/2.2/3.3 ms\n"
        )
        m = self.agent._PING_SENT_RE.search(out)
        self.assertEqual((m.group(1), m.group(2)), ("5", "5"))
        self.assertEqual(self.agent._PING_RTT_RE.search(out).group(1), "2.2")

    def test_aggregate(self) -> None:
        agg = self.agent._ru_ping_aggregate
        self.assertEqual(agg([]), (None, None))
        # Один якорь молчит, другой отвечает — мёртвый якорь не считаем потерями.
        self.assertEqual(
            agg([
                {"ip": "a", "sent": 5, "recv": 0, "avg_ms": None},
                {"ip": "b", "sent": 5, "recv": 5, "avg_ms": 40.0},
            ]),
            (40.0, 0.0),
        )
        # Молчат все — 100% потерь.
        self.assertEqual(
            agg([
                {"ip": "a", "sent": 5, "recv": 0, "avg_ms": None},
                {"ip": "b", "sent": 5, "recv": 0, "avg_ms": None},
            ]),
            (None, 100.0),
        )
        # Частичные потери складываются, RTT взвешен по ответам.
        self.assertEqual(
            agg([
                {"ip": "a", "sent": 5, "recv": 4, "avg_ms": 40.0},
                {"ip": "b", "sent": 5, "recv": 1, "avg_ms": 50.0},
            ]),
            (42.0, 50.0),
        )


if __name__ == "__main__":
    unittest.main()
