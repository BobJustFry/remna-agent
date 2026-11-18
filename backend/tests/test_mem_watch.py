"""Тесты памятьсторожа: пороги, защиты автоперезапуска, повторный разгон, сухой прогон."""
import asyncio
import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.services import mem_watch as mw
from app.services import subwatch

# 15:00 UTC = 18:00 МСК — день, вне тихого окна; 01:00 UTC = 04:00 МСК — окно.
DAY = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)
NIGHT = datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc)


def cfg(**kw) -> dict:
    return {**copy.deepcopy(mw.DEFAULTS), "confirm_ticks": 1, **kw}


def obs(name="ch-1", **kw) -> mw.Obs:
    base = dict(
        node_id="n1", name=name, host="1.2.3.4", answered=True,
        mem_total_mb=2000.0, mem_available_mb=300.0, swap_percent=10.0,   # 15% — тесно для RSS-тревоги, но не avail
        rss_mb=965.0, rss_source="agent", core_age_s=40 * 3600, has_core=True,
        panel_uuid="u-1", panel_connected=True, panel_uptime_s=40 * 3600,
    )
    base.update(kw)
    return mw.Obs(**base)


def alarmed(o=None, c=None, now=DAY, st=None):
    """Состояние после опроса, на котором тревога уже поднята."""
    c = c or cfg()
    o = o or obs()
    st = st if st is not None else mw.new_node_state()
    v, events = mw.advance(st, o, c, now)
    return st, v, events


class ThresholdTest(unittest.TestCase):
    def test_rss_limit_is_max_of_floor_and_percent(self):
        c = cfg()
        self.assertEqual(mw.rss_limit(1000, c), 350.0)    # 35% > 300
        self.assertEqual(mw.rss_limit(500, c), 300.0)     # пол 300
        self.assertEqual(mw.rss_limit(4000, c), 1400.0)

    def test_rss_over_limit_alarms(self):
        v = mw.verdict(obs(mem_total_mb=2000.0, rss_mb=800.0), cfg())
        self.assertIn("rss", v.kinds)

    def test_big_rss_without_pressure_is_quiet(self):
        # как pl-1: ядро 972 МБ из 2 ГБ, но свободно 24 %, своп 5 %
        v = mw.verdict(obs(mem_total_mb=1967.0, rss_mb=972.0, mem_available_mb=473.0, swap_percent=5.0), cfg())
        self.assertEqual(v.kinds, [])

    def test_big_rss_with_low_available_alarms(self):
        v = mw.verdict(obs(mem_total_mb=2000.0, rss_mb=900.0, mem_available_mb=350.0, swap_percent=5.0), cfg())
        self.assertEqual(v.kinds, ["rss"])

    def test_big_rss_with_swap_over_30_alarms(self):
        v = mw.verdict(obs(mem_total_mb=2000.0, rss_mb=900.0, mem_available_mb=900.0, swap_percent=35.0), cfg())
        self.assertEqual(v.kinds, ["rss"])

    def test_big_rss_unknown_pressure_still_alarms(self):
        v = mw.verdict(obs(mem_total_mb=2000.0, rss_mb=900.0, mem_available_mb=None, mem_percent=None,
                           swap_percent=None), cfg())
        self.assertEqual(v.kinds, ["rss"])

    def test_rss_under_limit_quiet(self):
        v = mw.verdict(obs(rss_mb=500.0, mem_available_mb=900.0), cfg())
        self.assertEqual(v.kinds, [])

    def test_avail_below_12_percent(self):
        v = mw.verdict(obs(rss_mb=100.0, mem_available_mb=200.0), cfg())   # 10%
        self.assertEqual(v.kinds, ["avail"])
        self.assertFalse(v.critical)

    def test_avail_below_5_percent_is_critical(self):
        v = mw.verdict(obs(rss_mb=100.0, mem_available_mb=60.0), cfg())    # 3%
        self.assertTrue(v.critical)

    def test_swap_over_60(self):
        v = mw.verdict(obs(rss_mb=100.0, swap_percent=61.0), cfg())
        self.assertEqual(v.kinds, ["swap"])
        self.assertEqual(mw.verdict(obs(rss_mb=100.0, swap_percent=60.0), cfg()).kinds, [])

    def test_oom_is_critical(self):
        v = mw.verdict(obs(rss_mb=100.0), cfg(), oom_active=True)
        self.assertIn("oom", v.kinds)
        self.assertTrue(v.critical)

    def test_avail_from_mem_percent_when_old_agent(self):
        o = obs(mem_available_mb=None, mem_percent=95.0, rss_mb=None)
        self.assertEqual(o.avail_pct, 5.0)

    def test_rss_unknown_still_judges_by_system_memory(self):
        v = mw.verdict(obs(rss_mb=None, mem_available_mb=100.0), cfg())
        self.assertEqual(v.kinds, ["avail"])
        self.assertFalse(v.rss_known)


class AlarmLifecycleTest(unittest.TestCase):
    def test_alarm_needs_confirmation_ticks(self):
        c = cfg(confirm_ticks=3)
        st = mw.new_node_state()
        for _ in range(2):
            _v, ev = mw.advance(st, obs(), c, DAY)
            self.assertEqual(ev, [])
        _v, ev = mw.advance(st, obs(), c, DAY)
        self.assertEqual([e["kind"] for e in ev], ["alarm"])

    def test_alarm_is_raised_once(self):
        st, _v, ev = alarmed()
        self.assertEqual([e["kind"] for e in ev], ["alarm"])
        _v, ev2 = mw.advance(st, obs(), cfg(), DAY + timedelta(seconds=30))
        self.assertEqual(ev2, [])

    def test_critical_skips_confirmation(self):
        c = cfg(confirm_ticks=50)
        st = mw.new_node_state()
        _v, ev = mw.advance(st, obs(rss_mb=100.0, mem_available_mb=40.0), c, DAY)
        self.assertEqual([e["kind"] for e in ev], ["alarm"])
        self.assertTrue(st["alarm"]["critical"])

    def test_escalation_to_critical_is_announced(self):
        st, _v, _ = alarmed()
        _v, ev = mw.advance(st, obs(mem_available_mb=40.0), cfg(), DAY + timedelta(seconds=30))
        self.assertEqual([e["kind"] for e in ev], ["alarm_critical"])

    def test_clear_after_quiet_ticks(self):
        c = cfg(clear_ticks=2)
        st, _v, _ = alarmed(c=c)
        good = obs(rss_mb=100.0, mem_available_mb=1500.0)
        _v, ev = mw.advance(st, good, c, DAY)
        self.assertEqual(ev, [])
        _v, ev = mw.advance(st, good, c, DAY)
        self.assertEqual([e["kind"] for e in ev], ["alarm_clear"])
        self.assertIsNone(st["alarm"])

    def test_remind_after_hours(self):
        st, _v, _ = alarmed()
        _v, ev = mw.advance(st, obs(), cfg(), DAY + timedelta(hours=6, minutes=1))
        self.assertEqual([e["kind"] for e in ev], ["alarm_remind"])

    def test_silent_agent_changes_nothing(self):
        st = mw.new_node_state()
        v, ev = mw.advance(st, obs(answered=False), cfg(), DAY)
        self.assertIsNone(v)
        self.assertEqual(ev, [])
        self.assertEqual(st["bad"], 0)


class DecisionTest(unittest.TestCase):
    def go(self, o=None, c=None, now=DAY, st=None, fleet=None, mutate=None):
        c = c or cfg()
        o = o or obs()
        st, v, _ = alarmed(o, c, now, st)
        if mutate:
            mutate(st)
        return mw.decide(o, st, v, fleet or mw.FleetCtx(), c, now), st

    def test_no_alarm_no_action(self):
        o = obs(rss_mb=100.0, mem_available_mb=1500.0)
        st = mw.new_node_state()
        v, _ = mw.advance(st, o, cfg(), DAY)
        self.assertEqual(mw.decide(o, st, v, mw.FleetCtx(), cfg(), DAY).action, "none")

    def test_noncritical_by_day_waits_for_window(self):
        d, _ = self.go()
        self.assertEqual(d.action, "wait_window")

    def test_noncritical_in_window_restarts(self):
        d, _ = self.go(now=NIGHT)
        self.assertEqual(d.action, "restart")

    def test_critical_restarts_by_day(self):
        d, _ = self.go(o=obs(mem_available_mb=40.0))
        self.assertEqual(d.action, "restart")

    def test_critical_still_respects_cooldown(self):
        last = DAY - timedelta(hours=3)
        d, _ = self.go(o=obs(mem_available_mb=40.0),
                       mutate=lambda st: st.update(last_restart_at=last.isoformat()))
        self.assertEqual(d.action, "blocked")
        self.assertIn("пауза", d.reason)

    def test_cooldown_expired_allows(self):
        last = DAY - timedelta(hours=13)
        d, _ = self.go(now=NIGHT, mutate=lambda st: st.update(
            last_restart_at=(NIGHT - timedelta(hours=13)).isoformat()))
        self.assertEqual(d.action, "restart")

    def test_young_core_blocks(self):
        d, _ = self.go(o=obs(panel_uptime_s=3 * 3600, mem_available_mb=40.0))
        self.assertEqual(d.action, "blocked")
        self.assertIn("ядро живёт", d.reason)

    def test_unknown_uptime_never_restarts(self):
        d, _ = self.go(o=obs(panel_uptime_s=None, core_age_s=None, mem_available_mb=40.0))
        self.assertEqual(d.action, "alarm_only")

    def test_per_node_24h_limit(self):
        fleet = mw.FleetCtx(restarts=[{"ts": (NIGHT - timedelta(hours=20)).isoformat(),
                                       "node_id": "n1", "source": "auto"}])
        d, _ = self.go(now=NIGHT, fleet=fleet)
        self.assertEqual(d.action, "blocked")
        self.assertIn("нужен человек", d.reason)

    def test_per_node_7d_limit(self):
        fleet = mw.FleetCtx(restarts=[
            {"ts": (NIGHT - timedelta(days=2)).isoformat(), "node_id": "n1", "source": "auto"},
            {"ts": (NIGHT - timedelta(days=5)).isoformat(), "node_id": "n1", "source": "auto"}])
        d, _ = self.go(now=NIGHT, fleet=fleet)
        self.assertEqual(d.action, "blocked")

    def test_manual_restarts_do_not_eat_auto_budget(self):
        fleet = mw.FleetCtx(restarts=[{"ts": (NIGHT - timedelta(days=2)).isoformat(),
                                       "node_id": "n1", "source": "manual"}])
        d, _ = self.go(now=NIGHT, fleet=fleet)
        self.assertEqual(d.action, "restart")

    def test_fleet_gap_10_minutes(self):
        fleet = mw.FleetCtx(restarts=[{"ts": (NIGHT - timedelta(minutes=5)).isoformat(),
                                       "node_id": "other", "source": "auto"}])
        d, _ = self.go(now=NIGHT, fleet=fleet)
        self.assertEqual(d.action, "wait")
        fleet = mw.FleetCtx(restarts=[{"ts": (NIGHT - timedelta(minutes=11)).isoformat(),
                                       "node_id": "other", "source": "auto"}])
        self.assertEqual(self.go(now=NIGHT, fleet=fleet)[0].action, "restart")

    def test_fleet_four_per_hour(self):
        rows = [{"ts": (NIGHT - timedelta(minutes=11 + 12 * i)).isoformat(),
                 "node_id": f"o{i}", "source": "auto"} for i in range(4)]
        d, _ = self.go(now=NIGHT, fleet=mw.FleetCtx(restarts=rows))
        self.assertEqual(d.action, "wait")
        self.assertIn("в час", d.reason)

    def test_one_at_a_time_while_other_is_checked(self):
        d, _ = self.go(now=NIGHT, fleet=mw.FleetCtx(pending_nodes=["other"]))
        self.assertEqual(d.action, "wait")

    def test_excluded_by_name(self):
        for name in ("HAProxy-1", "haproxy-2", "cn-1", "ua-1", "my-bot-node"):
            d, _ = self.go(o=obs(name=name, mem_available_mb=40.0))
            self.assertEqual(d.action, "alarm_only", name)
            self.assertIn("исключен", d.reason)

    def test_similar_name_is_not_excluded(self):
        self.assertFalse(mw.is_excluded("cn-10", cfg()))
        self.assertTrue(mw.is_excluded("CN-1", cfg()))

    def test_node_without_core_is_alarm_only(self):
        d, _ = self.go(o=obs(has_core=False, rss_mb=None, mem_available_mb=40.0))
        self.assertEqual(d.action, "alarm_only")

    def test_small_core_means_restart_would_not_help(self):
        d, _ = self.go(o=obs(rss_mb=120.0, mem_available_mb=40.0))
        self.assertEqual(d.action, "alarm_only")
        self.assertIn("не ядром", d.reason)

    def test_unknown_rss_is_alarm_only(self):
        d, _ = self.go(o=obs(rss_mb=None, mem_available_mb=40.0))
        self.assertEqual(d.action, "alarm_only")

    def test_panel_not_connected_waits(self):
        d, _ = self.go(o=obs(panel_connected=False, mem_available_mb=40.0))
        self.assertEqual(d.action, "wait")

    def test_panel_unknown_node_or_ambiguous_target(self):
        self.assertEqual(self.go(o=obs(panel_uuid=None, mem_available_mb=40.0))[0].action, "alarm_only")
        self.assertEqual(self.go(o=obs(panel_dup=True, mem_available_mb=40.0))[0].action, "alarm_only")

    def test_alarm_only_mode_never_restarts(self):
        d, _ = self.go(o=obs(mem_available_mb=40.0), c=cfg(auto_restart=False))
        self.assertEqual(d.action, "alarm_only")
        d, _ = self.go(now=NIGHT, c=cfg(auto_restart=False))
        self.assertEqual(d.action, "alarm_only")

    def test_pending_check_waits(self):
        d, _ = self.go(o=obs(mem_available_mb=40.0),
                       mutate=lambda st: st.update(pending={"at": DAY.isoformat()}))
        self.assertEqual(d.action, "wait")


class RelapseAndPostCheckTest(unittest.TestCase):
    def test_relapse_blocks_node_and_announces(self):
        st = mw.new_node_state()
        st["last_restart_at"] = (DAY - timedelta(hours=5)).isoformat()
        st, v, ev = alarmed(st=st)
        self.assertEqual([e["kind"] for e in ev], ["alarm", "relapse"])
        self.assertEqual(st["blocked"]["reason"], "relapse")
        d = mw.decide(obs(), st, v, mw.FleetCtx(), cfg(), DAY)
        self.assertEqual(d.action, "blocked")
        # Даже в тихое окно и при критичности — только человек.
        d = mw.decide(obs(mem_available_mb=30.0), st, v, mw.FleetCtx(), cfg(), NIGHT)
        self.assertEqual(d.action, "blocked")

    def test_no_relapse_after_24h(self):
        st = mw.new_node_state()
        st["last_restart_at"] = (DAY - timedelta(hours=30)).isoformat()
        st, _v, ev = alarmed(st=st)
        self.assertEqual([e["kind"] for e in ev], ["alarm"])
        self.assertIsNone(st["blocked"])

    def test_post_check_ok(self):
        c = cfg()
        st = mw.new_node_state()
        st["pending"] = {"at": DAY.isoformat(), "source": "auto"}
        o = obs(panel_uptime_s=120, rss_mb=90.0, mem_available_mb=1500.0)
        _v, ev = mw.advance(st, o, c, DAY + timedelta(seconds=160))
        self.assertEqual([e["kind"] for e in ev], ["restart_ok"])
        self.assertIsNone(st["pending"])

    def test_post_check_waits_before_deadline(self):
        st = mw.new_node_state()
        st["pending"] = {"at": DAY.isoformat(), "source": "auto"}
        o = obs(panel_uptime_s=40 * 3600, rss_mb=900.0)   # аптайм не сбросился
        _v, ev = mw.advance(st, o, cfg(), DAY + timedelta(seconds=200))
        self.assertNotIn("restart_failed", [e["kind"] for e in ev])
        self.assertIsNotNone(st["pending"])

    def test_post_check_failure_blocks_without_retry(self):
        st = mw.new_node_state()
        st["pending"] = {"at": DAY.isoformat(), "source": "auto"}
        o = obs(panel_uptime_s=40 * 3600, panel_connected=False)
        _v, ev = mw.advance(st, o, cfg(), DAY + timedelta(seconds=500))
        self.assertIn("restart_failed", [e["kind"] for e in ev])
        self.assertEqual(st["blocked"]["reason"], "post_check")
        self.assertIsNone(st["pending"])


class WindowTest(unittest.TestCase):
    def test_window_msk(self):
        c = cfg()
        self.assertTrue(mw._in_window(datetime(2026, 10, 17, 0, 30, tzinfo=timezone.utc), c))   # 03:30
        self.assertTrue(mw._in_window(datetime(2026, 10, 17, 2, 59, tzinfo=timezone.utc), c))   # 05:59
        self.assertFalse(mw._in_window(datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc), c))   # 06:00
        self.assertFalse(mw._in_window(datetime(2026, 10, 17, 23, 0, tzinfo=timezone.utc), c))  # 02:00

    def test_defaults_match_the_brief(self):
        d = mw.DEFAULTS
        self.assertTrue(d["dry_run"])          # первые сутки — сухой прогон
        self.assertTrue(d["auto_restart"])
        self.assertEqual((d["cooldown_h"], d["min_core_uptime_h"]), (12, 12))
        self.assertEqual((d["max_per_node_24h"], d["max_per_node_7d"]), (1, 2))
        self.assertEqual((d["fleet_gap_min"], d["fleet_per_hour"]), (10, 4))
        self.assertEqual((d["rss_min_mb"], d["rss_pct"], d["avail_pct"], d["swap_pct"]), (300, 35, 12, 60))

    def test_clean_config_clamps(self):
        c = mw.clean_config({**mw.DEFAULTS, "cooldown_h": 0, "fleet_per_hour": 999, "exclude": [" a ", "a", ""]})
        self.assertEqual(c["cooldown_h"], 1)
        self.assertEqual(c["fleet_per_hour"], 12)
        self.assertEqual(c["exclude"], ["a"])


# ── тик с поддельной базой ───────────────────────────────────────────────────

class FakeDb:
    def __init__(self):
        self.store: dict[str, dict] = {}
        self.commits = 0

    async def commit(self):
        self.commits += 1


class TickTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()

        async def get_setting(_db, key, fallback):
            data = self.db.store.get(key)
            return {**fallback, **copy.deepcopy(data)} if data is not None else dict(fallback)

        async def save_setting(_db, key, data):
            self.db.store[key] = copy.deepcopy(data)

        async def no_bot(_db):
            return ""

        self.sent: list[str] = []
        patches = [
            mock.patch.object(subwatch, "_json_setting", get_setting),
            mock.patch.object(subwatch, "_save_json", save_setting),
            mock.patch.object(subwatch, "bot_token", no_bot),
            mock.patch.object(mw, "_panel_restart", side_effect=self._restart),
        ]
        self.restarts: list[str] = []
        self.restart_error: str | None = None
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _restart(self, uuid):
        self.restarts.append(uuid)
        return self.restart_error

    def run_tick(self, c, observations, now):
        return asyncio.run(mw.tick(self.db, c, observations, None, now))

    def log_kinds(self):
        return [r["kind"] for r in self.db.store.get(mw.LOG_KEY, {}).get("rows", [])]

    def test_dry_run_never_restarts_but_reports_plan_once(self):
        c = cfg(dry_run=True)
        o = lambda: [obs(mem_available_mb=40.0)]
        rows = self.run_tick(c, o(), NIGHT)
        self.assertEqual(self.restarts, [])
        self.assertIn("restart_plan", [r["kind"] for r in rows])
        self.run_tick(c, o(), NIGHT + timedelta(seconds=30))
        self.assertEqual(self.log_kinds().count("restart_plan"), 1)
        state = self.db.store[mw.STATE_KEY]
        self.assertEqual(state["restarts"], [])             # сухой прогон лимитов не тратит
        self.assertEqual(state["nodes"]["n1"]["decision"]["action"], "restart")

    def test_live_run_restarts_with_before_and_after_records(self):
        c = cfg(dry_run=False)
        self.run_tick(c, [obs(mem_available_mb=40.0)], NIGHT)
        self.assertEqual(self.restarts, ["u-1"])
        kinds = self.log_kinds()
        self.assertEqual(kinds[:2], ["alarm", "restart_start"])
        state = self.db.store[mw.STATE_KEY]
        self.assertEqual(len(state["restarts"]), 1)
        self.assertEqual(state["nodes"]["n1"]["pending"]["source"], "auto")
        # Через 3 минуты нода вернулась — запись «ПОСЛЕ».
        back = obs(mem_available_mb=1500.0, rss_mb=90.0, panel_uptime_s=100)
        self.run_tick(c, [back], NIGHT + timedelta(seconds=180))
        self.assertIn("restart_ok", self.log_kinds())

    def test_second_tick_does_not_restart_again(self):
        c = cfg(dry_run=False)
        self.run_tick(c, [obs(mem_available_mb=40.0)], NIGHT)
        self.run_tick(c, [obs(mem_available_mb=40.0)], NIGHT + timedelta(seconds=30))
        self.run_tick(c, [obs(mem_available_mb=40.0, panel_uptime_s=20 * 3600)], NIGHT + timedelta(minutes=30))
        self.assertEqual(len(self.restarts), 1)

    def test_only_one_restart_per_tick_in_priority_order(self):
        c = cfg(dry_run=False)
        a = obs(name="a", node_id="a", mem_available_mb=40.0, rss_mb=900.0, panel_uuid="ua")
        b = obs(name="b", node_id="b", mem_available_mb=40.0, rss_mb=1100.0, panel_uuid="ub")
        self.run_tick(c, [a, b], NIGHT)
        self.assertEqual(self.restarts, ["ub"])               # тяжелее — первым
        state = self.db.store[mw.STATE_KEY]
        self.assertEqual(state["nodes"]["a"]["decision"]["action"], "wait")

    def test_alarm_without_bot_still_in_journal(self):
        c = cfg(dry_run=True, auto_restart=False)
        rows = self.run_tick(c, [obs()], DAY)
        self.assertEqual([r["kind"] for r in rows], ["alarm"])
        self.assertFalse(rows[0]["notified"])                 # бота нет — тревога только в UI и журнале
        self.assertIn("alarm", self.log_kinds())

    def test_panel_error_blocks_node_and_does_not_retry(self):
        c = cfg(dry_run=False)
        self.restart_error = "Remnawave API 500"
        self.run_tick(c, [obs(mem_available_mb=40.0)], NIGHT)
        self.run_tick(c, [obs(mem_available_mb=40.0)], NIGHT + timedelta(seconds=30))
        self.assertEqual(len(self.restarts), 1)
        self.assertIn("restart_error", self.log_kinds())
        self.assertEqual(self.db.store[mw.STATE_KEY]["nodes"]["n1"]["blocked"]["reason"], "api_error")

    def test_alarm_only_mode_full_cycle(self):
        c = cfg(dry_run=False, auto_restart=False)
        self.run_tick(c, [obs(mem_available_mb=40.0)], NIGHT)
        self.assertEqual(self.restarts, [])
        self.assertEqual(self.db.store[mw.STATE_KEY]["nodes"]["n1"]["decision"]["action"], "alarm_only")

    def test_relapse_after_restart_alarms_and_blocks(self):
        c = cfg(dry_run=False, clear_ticks=1)
        self.run_tick(c, [obs(mem_available_mb=40.0)], NIGHT)
        # вернулась, здорова — тревога снята
        good = obs(mem_available_mb=1500.0, rss_mb=90.0, panel_uptime_s=100)
        self.run_tick(c, [good], NIGHT + timedelta(seconds=180))
        self.run_tick(c, [good], NIGHT + timedelta(seconds=210))
        # через 5 часов память снова выше порога — тревога «повторный разгон», перезапуска нет
        later = NIGHT + timedelta(hours=5)
        self.run_tick(c, [obs(mem_available_mb=40.0, panel_uptime_s=5 * 3600)], later)
        self.assertEqual(len(self.restarts), 1)
        self.assertIn("relapse", self.log_kinds())
        self.assertEqual(self.db.store[mw.STATE_KEY]["nodes"]["n1"]["blocked"]["reason"], "relapse")

    def test_reset_block(self):
        c = cfg(dry_run=False)
        self.restart_error = "boom"
        self.run_tick(c, [obs(mem_available_mb=40.0)], NIGHT)
        self.assertTrue(asyncio.run(mw.reset_block(self.db, "n1")))
        self.assertIsNone(self.db.store[mw.STATE_KEY]["nodes"]["n1"]["blocked"])
        self.assertFalse(asyncio.run(mw.reset_block(self.db, "n1")))

    def test_manual_restart_is_journaled_and_requires_connected(self):
        asyncio.run(mw.manual_restart(self.db, None, obs(panel_connected=False), NIGHT))
        self.assertEqual(self.restarts, [])
        res = asyncio.run(mw.manual_restart(self.db, None, obs(), NIGHT))
        self.assertTrue(res["ok"])
        self.assertEqual(self.restarts, ["u-1"])
        self.assertIn("manual_restart", self.log_kinds())
        state = self.db.store[mw.STATE_KEY]
        self.assertEqual(state["restarts"][0]["source"], "manual")


class SshProbeTest(unittest.TestCase):
    def test_parse_ps_takes_heaviest(self):
        rss, age = mw.parse_ps("  98304  1234\n 987654  5000\n")
        self.assertEqual(age, 5000)
        self.assertAlmostEqual(rss, 964.5, places=1)

    def test_parse_ps_empty(self):
        self.assertEqual(mw.parse_ps(""), (None, None))
        self.assertEqual(mw.parse_ps("garbage"), (None, None))


if __name__ == "__main__":
    unittest.main()
