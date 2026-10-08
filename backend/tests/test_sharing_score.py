"""Тесты оценки шаринга. Ни панели, ни базы — только арифметика вердикта.

Главное, что здесь проверяется: семья из трёх устройств не краснеет, а ферма
из десятка адресов при лимите 1 краснеет, и тот же десяток при оплаченном
безлимите — уже нет.
"""
import unittest
from datetime import datetime, timedelta, timezone

from app.services import sharing_score
from app.services.sharing_score import AMBER, GREEN, RED, Features

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
CFG = dict(sharing_score.DEFAULTS)


def person(**kw) -> Features:
    """Один человек с телефоном: адрес меняется, но не одновременно."""
    base = dict(user_id=1, username="one", raw_limit=1,
                ips_5m=2, ips_15m=4, conc_ips=1, conc_nets=1)
    base.update(kw)
    return Features(**base)


def family(**kw) -> Features:
    """Семья: лимит 3, трое дома и в дороге, у каждого Wi-Fi и LTE."""
    base = dict(user_id=2, username="family", raw_limit=3,
                ips_5m=6, ips_15m=10, conc_ips=4, conc_nets=3,
                platforms=["ios", "android", "windows"], hwid_active=3, hwid_total=4)
    base.update(kw)
    return Features(**base)


def farm(**kw) -> Features:
    """Ферма: лимит 1, десяток адресов из разных сетей разом."""
    base = dict(user_id=3, username="farm", raw_limit=1,
                ips_5m=12, ips_15m=18, conc_ips=10, conc_nets=7,
                platforms=["ios", "android", "windows"], hwid_active=6, hwid_total=9)
    base.update(kw)
    return Features(**base)


class LimitTests(unittest.TestCase):
    def test_empty_limit_falls_back(self) -> None:
        self.assertEqual(sharing_score.device_limit(CFG, None),
                         (CFG["fallback_device_limit"], False))

    def test_zero_means_unlimited(self) -> None:
        limit, unlimited = sharing_score.device_limit(CFG, 0)
        self.assertEqual(limit, CFG["unlimited_as"])
        self.assertTrue(unlimited)

    def test_own_limit_wins(self) -> None:
        self.assertEqual(sharing_score.device_limit(CFG, 5), (5, False))

    def test_norms_grow_with_limit(self) -> None:
        small = sharing_score.norms(CFG, 1)
        big = sharing_score.norms(CFG, 5)
        for key in small:
            self.assertLess(small[key], big[key], key)

    def test_old_thresholds_are_the_norm_at_limit_three(self) -> None:
        # Старый жёсткий порог был рассчитан ровно на общий лимит 3 — новая
        # норма при том же лимите не должна оказаться строже на ровном месте.
        n = sharing_score.norms(CFG, 3)
        self.assertEqual(n["ips_15m"], 12)
        self.assertEqual(n["ips_5m"], 8)


class ScoreTests(unittest.TestCase):
    def test_single_person_is_green(self) -> None:
        v = sharing_score.score(CFG, person())
        self.assertEqual(v.level, GREEN)
        self.assertEqual(v.score, 0)

    def test_family_of_three_is_green(self) -> None:
        v = sharing_score.score(CFG, family())
        self.assertEqual(v.level, GREEN, v.reasons)
        self.assertLess(v.score, CFG["amber"])

    def test_farm_of_ten_is_red(self) -> None:
        v = sharing_score.score(CFG, farm())
        self.assertEqual(v.level, RED, v.reasons)
        self.assertGreaterEqual(v.score, CFG["red"])
        self.assertTrue(v.reasons)

    def test_same_farm_with_paid_unlimited_is_not_red(self) -> None:
        # Безлимит — платная услуга: тот же десяток адресов у такого человека
        # законен, и красным он быть не должен.
        v = sharing_score.score(CFG, farm(raw_limit=0))
        self.assertTrue(v.unlimited)
        self.assertNotEqual(v.level, RED, v.reasons)

    def test_mobile_person_switching_towers_stays_green(self) -> None:
        # Адреса меняются, но не одновременно — это не шаринг.
        v = sharing_score.score(CFG, person(ips_5m=5, ips_15m=9, conc_ips=2, conc_nets=1))
        self.assertEqual(v.level, GREEN, v.reasons)

    def test_three_platforms_at_limit_one_adds_full_weight(self) -> None:
        plain = sharing_score.score(CFG, person())
        with_pl = sharing_score.score(
            CFG, person(platforms=["ios", "android", "windows"], hwid_active=1, hwid_total=3))
        self.assertEqual(with_pl.score - plain.score, CFG["w_platforms"])

    def test_three_platforms_at_limit_three_add_nothing(self) -> None:
        v = sharing_score.score(CFG, family())
        self.assertNotIn("platforms", [p.key for p in v.parts])

    def test_two_countries_are_a_weak_signal(self) -> None:
        v = sharing_score.score(CFG, person(conc_countries=["RU", "DE"]))
        part = next(p for p in v.parts if p.key == "travel")
        self.assertLess(part.points, CFG["w_travel"])
        self.assertIn("слабый", part.why)

    def test_night_pause_silences_the_signal(self) -> None:
        quiet = sharing_score.score(CFG, person(night_busy_pct=3.0, night_days=7))
        self.assertNotIn("night", [p.key for p in quiet.parts])

    def test_no_night_pause_costs_points(self) -> None:
        busy = sharing_score.score(CFG, person(night_busy_pct=80.0, night_days=7))
        self.assertEqual(round(next(p.points for p in busy.parts if p.key == "night")),
                         CFG["w_night"])

    def test_night_signal_waits_for_enough_days(self) -> None:
        v = sharing_score.score(CFG, person(night_busy_pct=80.0, night_days=1))
        self.assertNotIn("night", [p.key for p in v.parts])

    def test_traffic_below_the_multiple_is_free(self) -> None:
        v = sharing_score.score(CFG, person(traffic_ratio=3.0))
        self.assertNotIn("traffic", [p.key for p in v.parts])

    def test_score_never_exceeds_hundred(self) -> None:
        v = sharing_score.score(CFG, farm(
            ips_5m=90, ips_15m=120, conc_ips=60, conc_nets=40,
            conc_countries=["RU", "DE", "US"], hwid_active=30, hwid_total=40,
            night_busy_pct=95.0, night_days=10, traffic_ratio=40.0))
        self.assertEqual(v.score, 100)

    def test_reasons_are_sorted_by_weight(self) -> None:
        v = sharing_score.score(CFG, farm())
        points = [p.points for p in v.parts]
        self.assertEqual(points, sorted(points, reverse=True))

    def test_amber_sits_between_the_zones(self) -> None:
        self.assertEqual(sharing_score.level_of(CFG, CFG["amber"]), AMBER)
        self.assertEqual(sharing_score.level_of(CFG, CFG["amber"] - 1), GREEN)
        self.assertEqual(sharing_score.level_of(CFG, CFG["red"]), RED)


class StreakTests(unittest.TestCase):
    def test_one_red_scan_is_not_an_alarm(self) -> None:
        state, event = sharing_score.step_user(None, CFG, RED, 70, T0)
        self.assertIsNone(event)
        self.assertFalse(state["alerted"])

    def test_three_red_scans_in_a_row_alarm(self) -> None:
        state, event = sharing_score.step_user(None, CFG, RED, 70, T0)
        state, event = sharing_score.step_user(state, CFG, RED, 72, T0 + timedelta(minutes=2))
        self.assertIsNone(event)
        state, event = sharing_score.step_user(state, CFG, RED, 71, T0 + timedelta(minutes=4))
        self.assertEqual(event, "alert")
        self.assertTrue(state["alerted"])

    def test_wifi_to_lte_hop_never_alarms(self) -> None:
        # Один красный скан, потом снова зелень — именно от этого выдержка.
        state, _ = sharing_score.step_user(None, CFG, RED, 70, T0)
        state, event = sharing_score.step_user(state, CFG, GREEN, 5, T0 + timedelta(minutes=2))
        self.assertIsNone(event)
        state, event = sharing_score.step_user(state, CFG, RED, 70, T0 + timedelta(minutes=4))
        self.assertIsNone(event)
        self.assertFalse(state["alerted"])

    def test_reminder_waits_the_configured_hours(self) -> None:
        state = None
        for i in range(3):
            state, event = sharing_score.step_user(state, CFG, RED, 70, T0 + timedelta(minutes=2 * i))
        self.assertEqual(event, "alert")
        state, event = sharing_score.step_user(state, CFG, RED, 70, T0 + timedelta(hours=1))
        self.assertIsNone(event)
        state, event = sharing_score.step_user(state, CFG, RED, 70, T0 + timedelta(hours=25))
        self.assertEqual(event, "remind")

    def test_clear_after_enough_green_scans(self) -> None:
        state = None
        for i in range(3):
            state, _ = sharing_score.step_user(state, CFG, RED, 70, T0 + timedelta(minutes=2 * i))
        for i in range(2):
            state, event = sharing_score.step_user(state, CFG, GREEN, 0, T0 + timedelta(hours=1, minutes=i))
            self.assertIsNone(event)
        state, event = sharing_score.step_user(state, CFG, GREEN, 0, T0 + timedelta(hours=2))
        self.assertEqual(event, "clear")
        self.assertFalse(state["alerted"])

    def test_amber_keeps_the_red_streak_but_does_not_feed_it(self) -> None:
        state, _ = sharing_score.step_user(None, CFG, RED, 70, T0)
        state, _ = sharing_score.step_user(state, CFG, AMBER, 40, T0 + timedelta(minutes=2))
        self.assertEqual(state["red"], 1)


class QuietWindowTests(unittest.TestCase):
    @staticmethod
    def day(busy_hours: set[int], scans: int = 30, hits: int = 30) -> dict:
        return {h: (scans, hits if h in busy_hours else 0) for h in range(24)}

    def test_needs_a_full_circle_of_hours(self) -> None:
        self.assertIsNone(sharing_score.quiet_window({h: (30, 10) for h in range(10)}))

    def test_sleeper_has_a_quiet_window(self) -> None:
        awake = set(range(8, 24))      # спит с 0 до 8
        self.assertEqual(sharing_score.quiet_window(self.day(awake)), 0.0)

    def test_round_the_clock_has_none(self) -> None:
        self.assertEqual(sharing_score.quiet_window(self.day(set(range(24)))), 100.0)

    def test_window_rolls_over_midnight(self) -> None:
        # Спит с 22 до 02 — окно пересекает полночь, и его надо найти.
        awake = set(range(2, 22))
        self.assertEqual(sharing_score.quiet_window(self.day(awake), width=4), 0.0)


if __name__ == "__main__":
    unittest.main()
