import importlib.util
import unittest
from pathlib import Path

from app.services.counter_delta import (
    CounterSnapshot,
    CounterTracker,
    counter_deltas,
    retr_low_volume,
    retr_pct,
    retr_pct_data,
    retr_point,
)

EMPTY = {
    "tcp_out_segs": None,
    "tcp_retrans_segs": None,
    "tcp_syn_retrans": None,
    "tcp_orig_data_sent": None,
    "oom_kills": None,
}


def snap(boot="b1", out=1000, retr=10, oom=0, syn=None, orig=None) -> CounterSnapshot:
    return CounterSnapshot(
        boot_id=boot,
        tcp_out=out,
        tcp_retrans=retr,
        oom_kill=oom,
        tcp_syn_retrans=syn,
        tcp_orig_data=orig,
    )


class CounterDeltaTests(unittest.TestCase):
    def test_first_tick_is_empty(self) -> None:
        d = counter_deltas(None, snap())
        self.assertEqual(d, EMPTY)

    def test_plain_delta(self) -> None:
        d = counter_deltas(snap(out=1000, retr=10, oom=1), snap(out=5000, retr=50, oom=3))
        self.assertEqual(d, {**EMPTY, "tcp_out_segs": 4000, "tcp_retrans_segs": 40, "oom_kills": 2})

    def test_new_counters_delta(self) -> None:
        d = counter_deltas(
            snap(out=1000, retr=10, syn=4, orig=600),
            snap(out=5000, retr=50, syn=30, orig=3600),
        )
        self.assertEqual(d["tcp_syn_retrans"], 26)
        self.assertEqual(d["tcp_orig_data_sent"], 3000)

    def test_new_counters_follow_old_pair(self) -> None:
        # Старая пара пустая (тот же отсчёт) — новая тоже, даже если бы сошлась.
        d = counter_deltas(snap(syn=4, orig=600), snap(syn=4, orig=600))
        self.assertIsNone(d["tcp_syn_retrans"])
        self.assertIsNone(d["tcp_orig_data_sent"])

    def test_new_counters_one_missing(self) -> None:
        # Одного из новых счётчиков нет — оба пусты, остаётся старая формула.
        d = counter_deltas(
            snap(out=1000, retr=10, syn=4, orig=None),
            snap(out=5000, retr=50, syn=9, orig=None),
        )
        self.assertEqual(d["tcp_out_segs"], 4000)
        self.assertIsNone(d["tcp_syn_retrans"])
        self.assertIsNone(d["tcp_orig_data_sent"])

    def test_new_counters_reset(self) -> None:
        # Отрицательная разница у новых — сброс, старая пара живёт сама по себе.
        d = counter_deltas(
            snap(boot=None, out=1000, retr=10, syn=500, orig=90000),
            snap(boot=None, out=5000, retr=50, syn=3, orig=100),
        )
        self.assertEqual(d["tcp_out_segs"], 4000)
        self.assertIsNone(d["tcp_syn_retrans"])
        self.assertIsNone(d["tcp_orig_data_sent"])

    def test_zero_data_sent_is_kept(self) -> None:
        # Нода только отвечает сканерам: OutSegs растёт, данных — ноль. Это правда.
        d = counter_deltas(
            snap(out=1000, retr=10, syn=10, orig=500),
            snap(out=1300, retr=40, syn=40, orig=500),
        )
        self.assertEqual(d["tcp_orig_data_sent"], 0)
        self.assertEqual(d["tcp_syn_retrans"], 30)

    def test_reboot_by_boot_id(self) -> None:
        # Новая загрузка: даже если счётчики успели перерасти старые — не верим.
        d = counter_deltas(
            snap(boot="b1", out=100, syn=1, orig=50),
            snap(boot="b2", out=900, retr=20, oom=1, syn=5, orig=600),
        )
        self.assertEqual(d, EMPTY)

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

    def test_agent_upgrade_mid_stream(self) -> None:
        # Агент обновился до 0.1.22: первый тик с новыми полями — новых дельт ещё
        # нет, старые считаются; со следующего есть всё.
        t = CounterTracker()
        t.update("n", snap(out=1000, retr=10))
        d = t.update("n", snap(out=2000, retr=20, syn=5, orig=700))
        self.assertEqual(d["tcp_out_segs"], 1000)
        self.assertIsNone(d["tcp_orig_data_sent"])
        d = t.update("n", snap(out=3000, retr=30, syn=7, orig=1400))
        self.assertEqual(d["tcp_syn_retrans"], 2)
        self.assertEqual(d["tcp_orig_data_sent"], 700)


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


class RetrPctDataTests(unittest.TestCase):
    def test_us1_case(self) -> None:
        # Тихая нода: 400 исходящих, из них 32 повтора, и почти все — SYN-ACK
        # сканерам. Старая формула — 8%, на данных — доли процента.
        self.assertEqual(retr_pct(32, 400), 8.0)
        self.assertEqual(retr_pct_data(32, 31, 1250), 0.08)

    def test_negative_numerator_is_zero(self) -> None:
        self.assertEqual(retr_pct_data(5, 9, 1000), 0.0)

    def test_empty_or_zero_data(self) -> None:
        self.assertIsNone(retr_pct_data(None, 0, 100))
        self.assertIsNone(retr_pct_data(5, None, 100))
        self.assertIsNone(retr_pct_data(5, 0, None))
        self.assertIsNone(retr_pct_data(5, 0, 0))

    def test_capped(self) -> None:
        self.assertEqual(retr_pct_data(500, 0, 100), 100.0)

    def test_decimal_from_postgres_sum(self) -> None:
        from decimal import Decimal

        self.assertEqual(retr_pct_data(Decimal(30), Decimal(10), Decimal(1000)), 2.0)


class RetrLowVolumeTests(unittest.TestCase):
    def test_threshold_five_minutes(self) -> None:
        self.assertTrue(retr_low_volume(19_999, 300, 20_000))
        self.assertFalse(retr_low_volume(20_000, 300, 20_000))

    def test_threshold_scales_with_time(self) -> None:
        # Шаг недели — 20 минут: порог 80 000.
        self.assertTrue(retr_low_volume(79_999, 1200, 20_000))
        self.assertFalse(retr_low_volume(80_000, 1200, 20_000))
        # Свежая точка покрыта на минуту: порог 4 000, а не полный.
        self.assertFalse(retr_low_volume(5_000, 60, 20_000))

    def test_unknown(self) -> None:
        self.assertIsNone(retr_low_volume(None, 300, 20_000))
        self.assertIsNone(retr_low_volume(100, 0, 20_000))

    def test_default_threshold(self) -> None:
        self.assertTrue(retr_low_volume(19_999, 300))
        self.assertFalse(retr_low_volume(20_000, 300))


class RetrPointTests(unittest.TestCase):
    def test_new_formula(self) -> None:
        pct, low = retr_point(
            retrans=100,
            out=10_000,
            retrans_new=100,
            syn_retrans=40,
            orig_data=30_000,
            covered_sec=300,
        )
        self.assertEqual(pct, 0.2)
        self.assertFalse(low)

    def test_new_formula_low_volume(self) -> None:
        pct, low = retr_point(
            retrans=32,
            out=400,
            retrans_new=32,
            syn_retrans=31,
            orig_data=1250,
            covered_sec=300,
        )
        self.assertEqual(pct, 0.08)
        self.assertTrue(low)

    def test_mixed_step_uses_only_new_samples(self) -> None:
        # В шаге агент обновился: ретрансмиты старых отсчётов (60 из 100) в
        # числитель новой формулы не идут.
        pct, _ = retr_point(
            retrans=100,
            out=10_000,
            retrans_new=40,
            syn_retrans=20,
            orig_data=20_000,
            covered_sec=150,
        )
        self.assertEqual(pct, 0.1)

    def test_fallback_to_old_formula(self) -> None:
        pct, low = retr_point(
            retrans=40,
            out=4000,
            retrans_new=None,
            syn_retrans=None,
            orig_data=None,
            covered_sec=0,
        )
        self.assertEqual(pct, 1.0)
        self.assertIsNone(low)

    def test_no_data_segments(self) -> None:
        # Новые поля есть, но данных за шаг ноль: доли нет, зато «мало данных».
        pct, low = retr_point(
            retrans=30,
            out=300,
            retrans_new=30,
            syn_retrans=30,
            orig_data=0,
            covered_sec=300,
        )
        self.assertIsNone(pct)
        self.assertTrue(low)

    def test_custom_threshold(self) -> None:
        _, low = retr_point(
            retrans=10,
            out=5000,
            retrans_new=10,
            syn_retrans=0,
            orig_data=5000,
            covered_sec=300,
            min_segs_5min=1000,
        )
        self.assertFalse(low)


def _load_agent():
    path = Path(__file__).resolve().parents[2] / "agent" / "remna_node_agent.py"
    spec = importlib.util.spec_from_file_location("remna_node_agent", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class AgentTcpExtTests(unittest.TestCase):
    agent = _load_agent()

    def test_reads_netstat(self) -> None:
        import builtins
        import io
        from unittest import mock

        text = (
            "TcpExt: SyncookiesSent TCPSynRetrans TCPOrigDataSent\n"
            "TcpExt: 0 17 123456\n"
            "IpExt: InNoRoutes\n"
            "IpExt: 0\n"
        )
        real_open = builtins.open

        def fake_open(path, *a, **kw):
            if path == "/proc/net/netstat":
                return io.StringIO(text)
            return real_open(path, *a, **kw)

        with mock.patch("builtins.open", fake_open):
            got = self.agent.tcp_ext_counters()
        self.assertEqual(got, {"tcp_syn_retrans_total": 17, "tcp_orig_data_sent_total": 123456})

    def test_missing_file(self) -> None:
        from unittest import mock

        with mock.patch("builtins.open", side_effect=OSError):
            got = self.agent.tcp_ext_counters()
        self.assertEqual(got, {"tcp_syn_retrans_total": None, "tcp_orig_data_sent_total": None})


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
