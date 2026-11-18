"""Fleet report: peak-based verdicts, formatting, schedule, command auth."""
import unittest
from datetime import date, datetime, timedelta, timezone

from app.services import fleet_bot as fb
from app.services import fleet_report as fr

CFG = fr.clean_config({})
D0 = date(2026, 10, 9)


def days(mems, cpu=10.0, net=None, online=100):
    return [fr.DayStat(day=D0 + timedelta(days=i), mem_max=m, mem_p95=m, cpu_max=cpu, rss_max=300,
                       net_p99_bps=net, online_max=online) for i, m in enumerate(mems)]


def prof(mems, total=2000.0, name="bg-2", cpu_max=30.0, bw=None, net=None, **kw):
    p = fr.NodeProfile(node_id=name, name=name, hosting="H", mem_total_mb=total,
                       bandwidth_mbps=bw, days=days(mems, net=net))
    p.mem_max = max(mems)
    sm = sorted(mems)
    p.mem_p95 = sm[int(len(sm) * 0.95) - 1]
    p.cpu_max, p.cpu_p95 = cpu_max, cpu_max * 0.8
    for k, v in kw.items():
        setattr(p, k, v)
    return p


class VerdictTest(unittest.TestCase):
    def test_quiet_day_after_busy_week_stays_red(self):
        # 14 days: evening p95 of 86-92 % on seven days, the last day is quiet (18 %).
        mems = [30, 88, 90, 35, 86, 92, 40, 87, 89, 33, 86, 30, 28, 18]
        p = prof(mems)
        p.cur_mem = 18.0          # the current sample is calm and must not matter
        v = fr.evaluate(p, CFG)
        self.assertEqual(v.level, "red")
        self.assertEqual(v.hot_days, 7)
        self.assertEqual(v.advice_gb, 4)
        self.assertGreater(v.required_mb, 1500)

    def test_old_50_percent_is_now_fine(self):
        self.assertEqual(fr.evaluate(prof([55, 60, 62, 58, 61, 57, 59, 60, 55, 62]), CFG).level, "ok")

    def test_p95_70_85_on_three_days_is_orange(self):
        v = fr.evaluate(prof([30, 72, 31, 75, 30, 78, 30, 29, 31, 30]), CFG)
        self.assertEqual(v.level, "orange")
        self.assertEqual(v.warm_days, 3)

    def test_two_warm_days_is_ok(self):
        self.assertEqual(fr.evaluate(prof([30, 32, 75, 31, 30, 72, 30, 29, 31, 30]), CFG).level, "ok")

    def test_two_red_days_is_orange_not_red(self):
        v = fr.evaluate(prof([30, 90, 72, 31, 91, 30, 29, 31, 30, 30]), CFG)
        self.assertEqual(v.level, "orange")

    def test_rising_trend_is_orange(self):
        v = fr.evaluate(prof([20, 22, 24, 25, 26, 27, 28, 35, 38, 42, 45, 46, 47, 48]), CFG)
        self.assertTrue(v.rising)
        self.assertEqual(v.level, "orange")

    def test_small_growth_or_low_level_is_not_trend(self):
        self.assertFalse(fr.evaluate(prof([40] * 7 + [46] * 7), CFG).rising)
        self.assertFalse(fr.evaluate(prof([5] * 7 + [20] * 7), CFG).rising)

    def test_oom_is_red(self):
        v = fr.evaluate(prof([30] * 10, oom_14d=2), CFG)
        self.assertEqual(v.level, "red")

    def test_thresholds_editable(self):
        cfg = fr.clean_config({"mem_red_p95": 60, "red_days": 2})
        self.assertEqual(fr.evaluate(prof([30, 62, 65, 30, 30, 30, 30, 30, 30, 30]), cfg).level, "red")

    def test_count_restarts_skips_manual_and_old(self):
        since = datetime(2026, 10, 10, tzinfo=timezone.utc)
        rs = [{"ts": "2026-10-20T10:00:00+00:00", "node_id": "a"},
              {"ts": "2026-10-21T10:00:00+00:00", "node_id": "a", "source": "manual"},
              {"ts": "2026-10-01T10:00:00+00:00", "node_id": "a"},
              {"ts": "2026-10-22T10:00:00+00:00", "node_id": "b"}]
        self.assertEqual(fr.count_restarts(rs, since), {"a": 1, "b": 1})

    def test_calm_node_ok(self):
        v = fr.evaluate(prof([30] * 14, total=1000.0), CFG)
        self.assertEqual(v.level, "ok")
        self.assertFalse(v.save)

    def test_save_on_big_idle_plan(self):
        v = fr.evaluate(prof([14, 15, 16, 12, 13, 15, 14, 13], total=4000.0, cpu_max=9.0), CFG)
        self.assertTrue(v.save)
        self.assertEqual(v.save_gb, 2)

    def test_no_save_on_small_plan(self):
        v = fr.evaluate(prof([10] * 10, total=900.0, cpu_max=5.0), CFG)
        self.assertFalse(v.save)

    def test_no_save_when_cpu_busy(self):
        v = fr.evaluate(prof([10] * 10, total=4000.0, cpu_max=40.0), CFG)
        self.assertFalse(v.save)

    def test_channel_needs_peaks_on_two_days(self):
        self.assertTrue(fr.evaluate(prof([30] * 10, bw=100.0, net=95e6), CFG).channel)
        self.assertFalse(fr.evaluate(prof([30] * 10, bw=100.0, net=50e6), CFG).channel)

    def test_too_little_history(self):
        self.assertEqual(fr.evaluate(prof([80, 80]), CFG).level, "nodata")

    def test_no_agent(self):
        self.assertEqual(fr.evaluate(prof([80] * 10, has_agent=False), CFG).level, "nodata")

    def test_nominal_sizes(self):
        self.assertEqual(fr.nominal_gb(960), 1)
        self.assertEqual(fr.nominal_gb(1900), 2)
        self.assertEqual(fr.nominal_gb(3900), 4)


def snap_of(*profiles):
    return fr.Snapshot(profiles=list(profiles),
                       verdicts={p.node_id: fr.evaluate(p, CFG) for p in profiles},
                       online_now=321, bps_now=2.5e9, up=len(profiles), total=len(profiles) + 1)


class FormatTest(unittest.TestCase):
    NOW = datetime(2026, 10, 23, 12, 0)

    def setUp(self):
        self.red = prof([30, 88, 90, 35, 86, 92, 40, 87, 89, 33], name="bg-2")
        self.idle = prof([14, 15, 16, 12, 13, 15, 14, 13], total=4000.0, cpu_max=9.0, name="de-9")
        self.fine = prof([30] * 10, total=1000.0, name="fi-1")
        self.down = prof([30] * 10, name="us-3", up=False)

    def test_full_sections_and_escape(self):
        self.red.name = "bg-2<x>"
        s = fr.build_report(snap_of(self.red, self.idle, self.fine, self.down), "full", self.NOW, {})
        for token in ("🔴", "Менять сейчас</b> (1)", "<pre>", "</pre>", "💤", "Можно сэкономить", "⚫",
                      "Не отвечают: us-3", "bg-2&lt;x&gt;", "2→4 ГБ · пам 90%", "4→2 ГБ",
                      "Онлайн 321 · 2.50 Гбит/с · ноды 4/5"):
            self.assertIn(token, s)
        self.assertNotIn("<x>", s)

    def test_full_rows_are_aligned_and_sorted(self):
        a = prof([30, 88, 90, 35, 86, 92, 40, 87, 89, 33], name="a-1")
        b = prof([30, 88, 99, 35, 86, 99, 40, 87, 89, 33], name="longer-9", oom_14d=7)
        s = fr.build_report(snap_of(a, b), "full", self.NOW, {})
        rows = [ln for ln in s.split("\n") if "ГБ ·" in ln]
        self.assertTrue(rows[0].startswith("longer-9  2→4 ГБ"))   # higher memory first
        self.assertIn("OOM ×7", rows[0])
        self.assertTrue(rows[1].startswith("a-1       2→4 ГБ"))

    def test_short_groups_by_action(self):
        a = prof([30, 88, 90, 35, 86, 92, 40, 87, 89, 33], name="pl-1")
        b = prof([30, 88, 90, 35, 86, 90, 40, 87, 89, 33], name="it-1", oom_14d=3)
        c = prof([30, 88, 90, 35, 86, 92, 40, 87, 89, 33], total=1000.0, name="lv-1")
        s = fr.build_report(snap_of(a, b, c, self.idle, self.fine, self.down), "short", self.NOW,
                            {"pl-1": "red"})
        self.assertIn("📊 <b>Парк</b> · 23.10 12:00 · онлайн 321", s)
        self.assertIn("🔴 Взять 2 ГБ → lv-1", s)
        self.assertRegex(s, r"🔴 Взять 4 ГБ → (pl-1 · it-1|it-1 · pl-1)")
        self.assertIn("💤 Можно сэкономить → de-9", s)
        self.assertIn("⚫ Не отвечают → us-3", s)
        self.assertIn("Остальные 3 нод в норме", s)
        self.assertIn("Хуже всех: ", s)
        self.assertIn("Чаще всех падают: it-1 ×3", s)
        self.assertIn("🟢 без изменений", s)
        self.assertNotIn("<pre>", s)

    def test_short_quiet(self):
        s = fr.build_report(snap_of(self.fine), "short", self.NOW, {"fi-1": "ok"})
        self.assertIn("без изменений", s)
        self.assertIn("Все 1 нод в норме", s)
        self.assertNotIn("Чаще всех падают", s)

    def test_short_shows_change(self):
        s = fr.build_report(snap_of(self.fine), "short", self.NOW, {"fi-1": "orange"})
        self.assertIn("Изменения: fi-1 🟠→🟢", s)

    def test_growth_note_instead_of_over_advice(self):
        # grows 18 points but a 4 GB node peaking at 44 % needs far less than 4 GB
        p = prof([20, 22, 24, 25, 26, 27, 28, 38, 40, 42, 44, 45, 46, 46], total=4000.0, name="kz-1")
        v = fr.evaluate(p, CFG)
        self.assertEqual(v.level, "orange")
        self.assertIsNone(v.advice_gb)
        s = fr.build_report(snap_of(p), "full", self.NOW, {})
        self.assertIn("kz-1  рост +", s)
        self.assertNotIn("→", s.split("Скоро")[1].split("</pre>")[0])

    def test_advice_is_smallest_fitting_size(self):
        # 2 GB node peaking at 90 % needs ~2.3 GB with headroom -> 4, never 8
        v = fr.evaluate(prof([90] * 10, total=2000.0), CFG)
        self.assertEqual(v.advice_gb, 4)
        # 1 GB node peaking at 60 % x1.3 = 780 MB would fit 1 GB: no upgrade
        v = fr.evaluate(prof([60] * 10, total=1000.0, oom_14d=1), CFG)
        self.assertEqual(v.level, "red")
        self.assertIsNone(v.advice_gb)

    def test_split_keeps_pre_balanced(self):
        txt = "head\n<pre>\n" + "\n".join(["x" * 100] * 80) + "\n</pre>\ntail"
        parts = fr.split_message(txt, 3000)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertEqual(p.count("<pre>"), p.count("</pre>"))
            self.assertLessEqual(len(p), 3000)

    def test_full_with_40_problem_nodes_fits_one_message(self):
        ps = [prof([90] * 10, name=f"node-{i:02d}", oom_14d=2) for i in range(40)]
        s = fr.build_report(snap_of(*ps), "full", self.NOW, {})
        self.assertLess(len(s), 4096)

    def test_day_close_block(self):
        t = fr.DayTotals(online_max=900, online_min=120, bps_avg=1e9, bps_peak=3e9)
        s = fr.build_report(snap_of(self.red), "full", self.NOW, {}, t, ["bg-2"],
                            {"bg-2": "4 ГБ / 2 яд."}, "22.10")
        self.assertIn("Итоги дня 22.10", s)
        self.assertIn("макс 900, мин 120", s)
        self.assertIn("bg-2 → 4 ГБ / 2 яд.", s)
        self.assertIn("Рестарты ядер по памяти: bg-2", s)

    def test_split_long(self):
        parts = fr.split_message("\n".join(["x" * 100] * 100))
        self.assertTrue(all(len(p) <= 3900 for p in parts))
        self.assertGreater(len(parts), 1)

    def test_status_text(self):
        a = prof([30] * 10, name="a", cur_mem=70.0, cur_cpu=5.0, cur_online=10, up=True,
                 bandwidth_mbps=100.0, cur_bps=9e7)
        b = prof([30] * 10, name="b", cur_mem=20.0, cur_cpu=5.0, cur_online=3, up=True)
        s = fr.build_status(snap_of(a, b), "14/14 хостов без проблем", "12:00", "15:00")
        self.assertIn("Топ-5 по памяти", s)
        self.assertIn("14/14", s)
        self.assertLess(s.index("a · "), s.index("b · "))

    def test_day_totals_ignores_partial_buckets(self):
        t = fr.day_totals([(100, 1e9, 10), (300, 2e9, 10), (5, 1e7, 2)])
        self.assertEqual((t.online_max, t.online_min), (300, 100))


class UnknownRamAndCapTest(unittest.TestCase):
    NOW = datetime(2026, 10, 23, 12, 0)

    def test_unknown_ram_gives_no_bogus_advice(self):
        p = prof([90] * 10, total=None, name="x-1")
        v = fr.evaluate(p, CFG)
        self.assertEqual(v.level, "red")
        self.assertIsNone(v.advice_gb)
        self.assertNotIn("→", fr.build_report(snap_of(p), "full", self.NOW, {}))



class ScheduleTest(unittest.TestCase):
    @staticmethod
    def utc(h, m=0):          # Moscow = UTC+3
        return datetime(2026, 10, 23, h, m, tzinfo=timezone.utc)

    def test_slots(self):
        self.assertEqual(fr.due_slot(self.utc(9, 5), CFG, None), ("2026-10-23 12", "full"))
        self.assertEqual(fr.due_slot(self.utc(3, 0), CFG, None), ("2026-10-23 06", "short"))
        self.assertEqual(fr.due_slot(self.utc(21, 10), CFG, None)[1], "full")   # 00:10 MSK next day
        self.assertIsNone(fr.due_slot(self.utc(10, 0), CFG, None))              # 13:00 is no slot

    def test_not_twice_and_catchup_window(self):
        self.assertIsNone(fr.due_slot(self.utc(9, 5), CFG, "2026-10-23 12"))
        self.assertIsNone(fr.due_slot(self.utc(9, 45), CFG, None))              # too late, skipped

    def test_next(self):
        self.assertEqual(fr.next_report(self.utc(9, 5), CFG).hour, 15)
        self.assertEqual(fr.next_report(self.utc(21, 5), CFG).hour, 6)

    def test_disabled_by_default(self):
        self.assertFalse(CFG["enabled"])

    def test_hours_parse(self):
        self.assertEqual(fr.parse_hours("12, 21;0 99 x", []), [0, 12, 21])


class CommandAuthTest(unittest.TestCase):
    @staticmethod
    def upd(chat, text):
        return {"update_id": 1, "message": {"chat": {"id": chat}, "text": text}}

    def test_allowed_chat(self):
        self.assertEqual(fb.extract_command(self.upd(-100123, "/status"), "-100123"),
                         ("status", "-100123"))
        self.assertEqual(fb.extract_command(self.upd(-100123, "/Report@mybot now"), "-100123")[0],
                         "report")

    def test_foreign_chat_ignored(self):
        self.assertIsNone(fb.extract_command(self.upd(555, "/status"), "-100123"))

    def test_no_chat_configured(self):
        self.assertIsNone(fb.extract_command(self.upd(555, "/status"), ""))

    def test_non_commands_and_unknown(self):
        self.assertIsNone(fb.extract_command(self.upd(1, "hello"), "1"))
        self.assertIsNone(fb.extract_command(self.upd(1, "/reboot"), "1"))
        self.assertIsNone(fb.extract_command({"update_id": 2}, "1"))
        self.assertIsNone(fb.extract_command({"message": {"text": "/status"}}, "1"))


def _at(i: int, hour_utc: int = 5) -> datetime:
    """UTC moment on day D0+i (05:00 UTC = 10:00 local at +5)."""
    d = D0 + timedelta(days=i)
    return datetime(d.year, d.month, d.day, hour_utc, tzinfo=timezone.utc)


def resized(mems, total, rec, now_i, ooms=None, restarts=None):
    """What load_snapshot does for one node: track the size, drop old days, recount peaks/OOMs."""
    p = prof(mems, total=total)
    for i, k in (ooms or {}).items():
        p.days[i].oom = k
    p.restart_days = [D0 + timedelta(days=i) for i in (restarts or [])]
    p.oom_14d = sum(d.oom for d in p.days) + len(p.restart_days)
    now = _at(now_i, 12)
    rec = fr.track_total(rec, total, now)
    today = fr.local_now(now, CFG).date()
    fr.apply_resize(p, fr.parse_since(rec), int(CFG["utc_offset"]), today,
                    int(CFG["window_days"]), rec.get("prev_mb"))
    return p, rec


class ResizeTest(unittest.TestCase):
    """After a RAM upgrade the old percentages must not be applied to the new size."""

    def test_track_total_ignores_kernel_jitter(self):
        rec = {"mb": 1968.0, "since": None}
        self.assertIs(fr.track_total(rec, 1892.6, _at(5)), rec)
        self.assertEqual(fr.track_total(None, 1968.0, _at(5)), {"mb": 1968.0, "since": None})
        ch = fr.track_total(rec, 3904.6, _at(5))
        self.assertEqual((ch["mb"], ch["prev_mb"]), (3904.6, 1968.0))
        self.assertEqual(fr.parse_since(ch), _at(5))

    def test_owner_case_2_to_4_no_more_advice(self):
        # 10 days at 95 % of 2 GB (+ OOMs), then 4 GB with 40-50 %
        mems = [95] * 10 + [45, 50, 40, 48]
        rec = {"mb": 3904.6, "prev_mb": 1968.0, "since": _at(9, 18).isoformat()}   # late on day 9 local
        p, rec = resized(mems, 3904.6, rec, 13, ooms={3: 2, 7: 1}, restarts=[8])
        self.assertEqual([d.day for d in p.days], [D0 + timedelta(days=i) for i in range(10, 14)])
        self.assertEqual(p.mem_max, 50)
        self.assertEqual(p.oom_14d, 0)                 # the OOMs were on the old size
        v = fr.evaluate(p, CFG)
        self.assertEqual(v.level, "ok")
        self.assertIsNone(v.advice_gb)
        self.assertLess(v.required_mb, 3904.6)         # 50 % of 4 GB + 30 %, real MB of the new peak
        self.assertFalse(v.resized)

    def test_owner_case_without_fix_would_advise_8(self):
        # the same node without a known change point: the old bug (4 -> 8) for reference
        p = prof([95] * 10 + [45, 50, 40, 48], total=3904.6)
        self.assertEqual(fr.evaluate(p, CFG).advice_gb, 8)

    def test_chain_1_2_4(self):
        # 1 GB at 97 % for 5 days, upgraded to 2 GB on day 4
        rec = {"mb": 960.0, "since": None}
        p, rec = resized([97, 96, 98, 97, 97], 1968.0, rec, 4)
        self.assertEqual(rec["prev_mb"], 960.0)
        self.assertTrue(fr.evaluate(p, CFG).resized)           # detected now: no days yet
        self.assertIsNone(fr.evaluate(p, CFG).advice_gb)
        p, rec = resized([97, 96, 98, 97, 97, 55, 50, 52, 54, 53], 1968.0, rec, 9)
        v = fr.evaluate(p, CFG)
        self.assertEqual((v.level, v.advice_gb, v.n_days), ("ok", None, 5))     # no "2 -> 4"
        # then 2 -> 4 GB on day 10
        rec = fr.track_total(rec, 3904.6, _at(10))
        self.assertEqual(rec["prev_mb"], 1968.0)
        p, rec = resized([97, 96, 98, 97, 97, 55, 50, 52, 54, 53, 30, 28, 31, 29], 3904.6, rec, 13)
        v = fr.evaluate(p, CFG)
        self.assertEqual((v.level, v.advice_gb), ("ok", None))                   # no "4 -> 8"
        self.assertEqual(p.mem_max, 31)

    def test_change_without_new_data_gives_no_advice(self):
        rec = {"mb": 1968.0, "since": None}
        p, rec = resized([95] * 14, 3904.6, rec, 13)          # detected today, nothing after it yet
        self.assertEqual(p.days, [])
        v = fr.evaluate(p, CFG)
        self.assertTrue(v.resized)
        self.assertEqual(v.level, "ok")
        self.assertIsNone(v.advice_gb)
        snap = fr.Snapshot(profiles=[p], verdicts={p.node_id: v}, online_now=None, bps_now=None,
                           up=1, total=1)
        self.assertEqual(fr.update_upgrades({"bg-2": "8 ГБ / 4 яд."}, snap), {})   # stale advice gone
        text = fr.build_report(snap, "full", datetime(2026, 10, 22, 12, 0), {})
        self.assertIn("ОЗУ сменили", text)
        self.assertIn("2→4 ГБ", text)

    def test_two_days_after_change_still_no_advice(self):
        rec = {"mb": 3904.6, "prev_mb": 1968.0, "since": _at(11).isoformat()}
        p, _ = resized([95] * 12 + [92, 94], 3904.6, rec, 13)
        v = fr.evaluate(p, CFG)
        self.assertEqual((v.n_days, v.resized, v.advice_gb), (2, True, None))

    def test_oom_after_change_is_red_without_advice(self):
        rec = {"mb": 3904.6, "prev_mb": 1968.0, "since": _at(12).isoformat()}
        p, _ = resized([95] * 13 + [60], 3904.6, rec, 13, ooms={5: 3, 13: 1})
        self.assertEqual(p.oom_14d, 1)
        v = fr.evaluate(p, CFG)
        self.assertEqual((v.level, v.advice_gb), ("red", None))

    def test_real_shortage_after_upgrade_is_advised(self):
        # 4 GB really short: 90 % for 3+ days after the change
        rec = {"mb": 3904.6, "prev_mb": 1968.0, "since": _at(9).isoformat()}
        p, _ = resized([95] * 10 + [90, 91, 90, 92], 3904.6, rec, 13)
        v = fr.evaluate(p, CFG)
        self.assertEqual(v.level, "red")
        self.assertEqual(v.advice_gb, 8)
        self.assertGreater(v.required_mb, 3904.6 * 0.9)

    def test_change_older_than_window_changes_nothing(self):
        rec = {"mb": 3904.6, "prev_mb": 1968.0, "since": (_at(0) - timedelta(days=30)).isoformat()}
        p, _ = resized([95] * 14, 3904.6, rec, 13)
        self.assertEqual(len(p.days), 14)
        self.assertIsNone(p.resized_day)

    def test_save_ignores_days_of_a_bigger_old_size(self):
        # downgraded 8 -> 4 GB: 10 % of 8 GB days must not suggest dropping to 1-2 GB
        rec = {"mb": 3904.6, "prev_mb": 7900.0, "since": _at(9).isoformat()}
        p, _ = resized([10] * 10 + [45, 50, 48, 47], 3904.6, rec, 13)
        v = fr.evaluate(p, CFG)
        self.assertFalse(v.save)
        self.assertIsNone(v.save_gb)


if __name__ == "__main__":
    unittest.main()
