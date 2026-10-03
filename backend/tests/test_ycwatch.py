"""Тесты следилки за Yandex Cloud. Облако не трогаем — проверяем решения."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services import ycwatch
from app.services.ycwatch import BLIND, CRITICAL, LOW, OK, Observation

T0 = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
CFG = dict(ycwatch.DEFAULTS)
RUNNING = [{"id": "vm1", "name": "sub", "status": "RUNNING", "ip": "84.201.189.60"}]


def obs(balance=781.75, vms=None, active=True) -> Observation:
    return Observation(ok=True, balance=balance, currency="RUB", account_active=active,
                       vms=RUNNING if vms is None else vms)


def fail(err="не достучались") -> Observation:
    return Observation(ok=False, error=err)


def empty_state() -> dict:
    return {"level": None, "since": None, "fails": 0, "last_notified": None,
            "checked_at": None, "reasons": []}


class AssessTests(unittest.TestCase):
    def test_all_good(self) -> None:
        self.assertEqual(ycwatch.assess(CFG, obs())[0], OK)

    def test_below_threshold_is_low(self) -> None:
        self.assertEqual(ycwatch.assess(CFG, obs(balance=299.0))[0], LOW)

    def test_zero_or_debt_is_critical(self) -> None:
        self.assertEqual(ycwatch.assess(CFG, obs(balance=0.0))[0], CRITICAL)
        self.assertEqual(ycwatch.assess(CFG, obs(balance=-1000.0))[0], CRITICAL)

    def test_stopped_vm_is_critical_even_with_money(self) -> None:
        stopped = [dict(RUNNING[0], status="STOPPED")]
        level, reasons = ycwatch.assess(CFG, obs(vms=stopped))
        self.assertEqual(level, CRITICAL)
        self.assertIn("STOPPED", reasons[0])

    def test_missing_vm_is_critical(self) -> None:
        self.assertEqual(ycwatch.assess(CFG, obs(vms=[]))[0], CRITICAL)

    def test_inactive_account_is_critical(self) -> None:
        self.assertEqual(ycwatch.assess(CFG, obs(active=False))[0], CRITICAL)


class StepTests(unittest.TestCase):
    def test_first_ok_is_silent(self) -> None:
        state, say, _ = ycwatch.step(empty_state(), CFG, obs(), T0)
        self.assertIsNone(say)
        self.assertEqual(state["level"], OK)

    def test_first_low_speaks(self) -> None:
        _, say, _ = ycwatch.step(empty_state(), CFG, obs(balance=100), T0)
        self.assertEqual(say, LOW)

    def test_low_reminds_daily_not_hourly(self) -> None:
        state, say, _ = ycwatch.step(empty_state(), CFG, obs(balance=100), T0)
        state, say, _ = ycwatch.step(state, CFG, obs(balance=95), T0 + timedelta(hours=1))
        self.assertIsNone(say)
        state, say, _ = ycwatch.step(state, CFG, obs(balance=50), T0 + timedelta(hours=24))
        self.assertEqual(say, LOW)

    def test_critical_reminds_hourly(self) -> None:
        state, say, _ = ycwatch.step(empty_state(), CFG, obs(balance=-5), T0)
        self.assertEqual(say, CRITICAL)
        state, say, _ = ycwatch.step(state, CFG, obs(balance=-6), T0 + timedelta(minutes=30))
        self.assertIsNone(say)
        state, say, _ = ycwatch.step(state, CFG, obs(balance=-7), T0 + timedelta(minutes=60))
        self.assertEqual(say, CRITICAL)
        self.assertNotIn("remind", state)

    def test_escalation_and_recovery_speak(self) -> None:
        state, _, _ = ycwatch.step(empty_state(), CFG, obs(balance=100), T0)
        state, say, _ = ycwatch.step(state, CFG, obs(balance=-1), T0 + timedelta(hours=1))
        self.assertEqual(say, CRITICAL)
        state, say, _ = ycwatch.step(state, CFG, obs(balance=1000), T0 + timedelta(hours=2))
        self.assertEqual(say, OK)
        state, say, _ = ycwatch.step(state, CFG, obs(balance=990), T0 + timedelta(hours=3))
        self.assertIsNone(say)

    def test_api_blind_only_after_streak(self) -> None:
        state, _, _ = ycwatch.step(empty_state(), CFG, obs(), T0)
        for i in range(1, ycwatch.FAIL_STREAK):
            state, say, _ = ycwatch.step(state, CFG, fail(), T0 + timedelta(hours=i))
            self.assertIsNone(say)
            self.assertEqual(state["level"], OK)
        state, say, _ = ycwatch.step(state, CFG, fail(), T0 + timedelta(hours=ycwatch.FAIL_STREAK))
        self.assertEqual(say, BLIND)
        # Пока слепы — не чаще раза в сутки.
        state, say, _ = ycwatch.step(state, CFG, fail(), T0 + timedelta(hours=ycwatch.FAIL_STREAK + 1))
        self.assertIsNone(say)
        state, say, _ = ycwatch.step(state, CFG, obs(), T0 + timedelta(hours=ycwatch.FAIL_STREAK + 2))
        self.assertEqual(say, OK)
        self.assertEqual(state["fails"], 0)

    def test_blind_reminder_fires(self) -> None:
        state = dict(empty_state(), level=BLIND, fails=5, last_notified=T0.isoformat())
        _, say, _ = ycwatch.step(state, CFG, fail(), T0 + timedelta(hours=25))
        self.assertEqual(say, BLIND)

    def test_single_hiccup_keeps_level(self) -> None:
        state, _, _ = ycwatch.step(empty_state(), CFG, obs(balance=-1), T0)
        state, say, _ = ycwatch.step(state, CFG, fail(), T0 + timedelta(minutes=10))
        self.assertIsNone(say)
        self.assertEqual(state["level"], CRITICAL)


class BurnTests(unittest.TestCase):
    def test_steady_burn(self) -> None:
        pts = [(T0 + timedelta(hours=h), 1000 - 2 * h) for h in range(25)]
        self.assertAlmostEqual(ycwatch.burn_per_day(pts), 48.0)

    def test_topup_not_counted_as_income(self) -> None:
        pts = [(T0 + timedelta(hours=h), 100 - h) for h in range(12)]
        pts += [(T0 + timedelta(hours=12 + h), 1000 - h) for h in range(13)]
        self.assertAlmostEqual(ycwatch.burn_per_day(pts), 23 / 24 * 24)

    def test_too_short_history(self) -> None:
        pts = [(T0, 100.0), (T0 + timedelta(hours=1), 99.0)]
        self.assertIsNone(ycwatch.burn_per_day(pts))


class KeyTests(unittest.TestCase):
    def _key(self) -> str:
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption()).decode()
        return json.dumps({"id": "ajeKEY", "service_account_id": "ajeSA",
                           "key_algorithm": "RSA_2048",
                           "private_key": "PLEASE DO NOT REMOVE THIS LINE! Yandex.Cloud SA Key ID <ajeKEY>\n" + pem})

    def test_parse_and_sign(self) -> None:
        key = ycwatch.parse_key(self._key())
        self.assertEqual(set(key), {"id", "service_account_id", "private_key"})
        token = ycwatch._jwt(key)
        self.assertEqual(token.count("."), 2)

    def test_garbage_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ycwatch.parse_key("{}")
        with self.assertRaises(ValueError):
            ycwatch.parse_key("not json")


if __name__ == "__main__":
    unittest.main()
