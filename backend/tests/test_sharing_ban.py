"""Тесты бана за шаринг: пороги автобана, защиты и пометка в комментарии."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.services import sharing_ban, sharing_score

T0 = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def cfg(**kw) -> dict:
    return {**sharing_score.DEFAULTS, "auto_ban": True, **kw}


def hit(uid=7, score=90, level="red") -> dict:
    return {"user_id": uid, "username": "u", "score": score, "level": level, "parts": []}


def alerted(red=5) -> dict:
    return {"alerted": True, "red": red}


class DefaultsTest(unittest.TestCase):
    def test_autoban_off_by_default(self):
        self.assertFalse(sharing_score.DEFAULTS["auto_ban"])
        self.assertEqual(sharing_score.DEFAULTS["auto_ban_score"], 85)
        self.assertEqual(sharing_score.DEFAULTS["auto_ban_scans"], 5)

    def test_disabled_never_bans(self):
        c = {**sharing_score.DEFAULTS}
        self.assertEqual(sharing_ban.auto_ban_decision(c, hit(), alerted(), "ACTIVE", 0)[0], "skip")


class DecisionTest(unittest.TestCase):
    def d(self, c=None, h=None, st="x", status="ACTIVE", recent=0):
        st = alerted() if st == "x" else st
        return sharing_ban.auto_ban_decision(c or cfg(), h or hit(), st, status, recent)[0]

    def test_all_conditions_ban(self):
        self.assertEqual(self.d(), "ban")

    def test_needs_alert(self):
        self.assertEqual(self.d(st={"alerted": False, "red": 9}), "skip")
        self.assertEqual(self.d(st=None), "skip")

    def test_score_threshold(self):
        self.assertEqual(self.d(h=hit(score=84)), "skip")
        self.assertEqual(self.d(h=hit(score=85)), "ban")

    def test_scans_threshold(self):
        self.assertEqual(self.d(st=alerted(4)), "skip")
        self.assertEqual(self.d(st=alerted(5)), "ban")
        self.assertEqual(self.d(c=cfg(auto_ban_scans=2), st=alerted(2)), "ban")

    def test_must_be_red_now(self):
        self.assertEqual(self.d(h=hit(score=90, level="amber")), "skip")

    def test_already_disabled(self):
        self.assertEqual(self.d(status="DISABLED"), "skip")

    def test_excluded(self):
        self.assertEqual(self.d(c=cfg(ban_exclude_ids=[7])), "skip")
        self.assertEqual(self.d(c=cfg(ban_exclude_ids=["7"])), "skip")
        self.assertEqual(self.d(c=cfg(ban_exclude_ids=[8])), "ban")

    def test_hourly_limit(self):
        self.assertEqual(self.d(recent=2), "ban")
        self.assertEqual(self.d(recent=3), "limit")


class HelpersTest(unittest.TestCase):
    def test_recent_window(self):
        rows = [
            {"action": "ban", "source": "auto", "ok": True, "ts": (T0 - timedelta(minutes=10)).isoformat()},
            {"action": "ban", "source": "auto", "ok": True, "ts": (T0 - timedelta(hours=2)).isoformat()},
            {"action": "ban", "source": "manual", "ok": True, "ts": T0.isoformat()},
            {"action": "ban", "source": "auto", "ok": False, "ts": T0.isoformat()},
            {"action": "unban", "source": "manual", "ok": True, "ts": T0.isoformat()},
        ]
        self.assertEqual(sharing_ban.recent_auto_bans(rows, T0), 1)

    def test_note_appended_not_overwritten(self):
        note = sharing_ban.ban_note(T0, 91, auto=False)
        self.assertEqual(note, "Забанен за шаринг 2026-10-16 12:00 UTC, оценка 91/100")
        out = sharing_ban.append_note("клиент Вася", note)
        self.assertTrue(out.startswith("клиент Вася | "))
        self.assertTrue(out.endswith(note))
        self.assertEqual(sharing_ban.append_note(None, note), note)

    def test_note_limit_keeps_new_note(self):
        note = sharing_ban.ban_note(T0, 91, auto=True)
        out = sharing_ban.append_note("x" * 5000, note, limit=200)
        self.assertLessEqual(len(out), 200)
        self.assertTrue(out.endswith(note))

    def test_banned_now(self):
        rows = [
            {"action": "ban", "user_id": 1, "ok": True},
            {"action": "ban", "user_id": 2, "ok": True},
            {"action": "unban", "user_id": 1, "ok": True},
            {"action": "ban", "user_id": 3, "ok": False},
            {"action": "ban", "user_id": 4, "ok": True, "skipped": "уже отключён"},
        ]
        self.assertEqual([r["user_id"] for r in sharing_ban.banned_now(rows)], [2])


class ProcessTest(unittest.TestCase):
    def run_process(self, c, suspects, users, log_rows=None, status="ACTIVE"):
        state = {"users": users}
        banned: list[int] = []

        async def get_state(db):
            return state

        async def save_state(db, s):
            state.update(s)

        async def get_log(db):
            return log_rows or []

        async def fake_ban(db, cfg_, h, source="manual", now=None):
            banned.append(h["user_id"])
            return {"ok": True, "steps": ["disable"]}

        async def notify(*a, **k):
            return None

        from app.services import sharing_detect
        cache = {"by_id": {s["user_id"]: {"status": status} for s in suspects}}
        with mock.patch.object(sharing_ban.sharing_watch, "get_state", get_state), \
             mock.patch.object(sharing_ban.sharing_watch, "save_state", save_state), \
             mock.patch.object(sharing_ban, "get_log", get_log), \
             mock.patch.object(sharing_ban, "ban", fake_ban), \
             mock.patch.object(sharing_ban, "_notify_text", notify), \
             mock.patch.object(sharing_detect, "users_cache", lambda: cache):
            done = asyncio.run(sharing_ban.process_auto_bans(None, c, suspects, T0))
        return banned, done, state

    def test_ban_and_state_cleared(self):
        banned, done, state = self.run_process(
            cfg(), [hit(7)], {"7": alerted(5)})
        self.assertEqual(banned, [7])
        self.assertNotIn("7", state["users"])

    def test_max_three_per_hour(self):
        suspects = [hit(i) for i in range(1, 6)]
        users = {str(i): alerted(6) for i in range(1, 6)}
        banned, done, state = self.run_process(cfg(), suspects, users)
        self.assertEqual(banned, [1, 2, 3])
        self.assertEqual([k for k, _ in done].count("limit"), 2)

    def test_prior_bans_count_toward_limit(self):
        rows = [{"action": "ban", "source": "auto", "ok": True,
                 "ts": (T0 - timedelta(minutes=5)).isoformat()}] * 3
        banned, done, _ = self.run_process(cfg(), [hit(7)], {"7": alerted(6)}, rows)
        self.assertEqual(banned, [])
        self.assertEqual(done[0][0], "limit")

    def test_off_means_nothing(self):
        banned, done, _ = self.run_process(
            {**sharing_score.DEFAULTS}, [hit(7)], {"7": alerted(9)})
        self.assertEqual((banned, done), ([], []))

    def test_already_disabled_not_banned(self):
        banned, _, _ = self.run_process(cfg(), [hit(7)], {"7": alerted(9)}, status="DISABLED")
        self.assertEqual(banned, [])


class PanelCallsTest(unittest.TestCase):
    def test_ban_calls_and_description(self):
        calls = []

        def fake(path, method="GET", body=None):
            calls.append((method, path, body))
            if method == "GET":
                return {"id": 7, "username": "u", "status": "ACTIVE", "description": "старое"}
            return {}

        with mock.patch.object(sharing_ban, "rw_api", fake):
            res = sharing_ban._panel_ban(7, "NOTE")
        self.assertTrue(res["ok"])
        self.assertEqual(calls[1], ("POST", "/api/users/7/actions/disable", {}))
        self.assertEqual(calls[2][1], "/api/connections/drop")
        self.assertEqual(calls[2][2], {"dropBy": {"by": "userIds", "userIds": [7]},
                                       "targetNodes": {"target": "allNodes"}})
        self.assertEqual(calls[3], ("PATCH", "/api/users", {"id": 7, "description": "старое | NOTE",
                                                   "tag": "SHARER"}))

    def _run(self, fn, user, *args):
        calls = []

        def fake(path, method="GET", body=None):
            calls.append((method, path, body))
            return user if method == "GET" else {}

        with mock.patch.object(sharing_ban, "rw_api", fake):
            res = fn(7, *args)
        return res, [c for c in calls if c[0] == "PATCH"]

    def test_ban_keeps_old_tag_in_comment(self):
        _, p = self._run(sharing_ban._panel_ban,
                         {"status": "ACTIVE", "description": "", "tag": "VIP"}, "NOTE")
        self.assertEqual(p[0][2]["tag"], "SHARER")
        self.assertIn("прежний тег: VIP", p[0][2]["description"])

    def test_unban_clears_sharer_tag(self):
        res, p = self._run(sharing_ban._panel_unban,
                           {"status": "DISABLED", "description": "a", "tag": "SHARER"}, "n")
        self.assertIn("tag", p[0][2])
        self.assertIsNone(p[0][2]["tag"])

    def test_unban_leaves_other_tag(self):
        res, p = self._run(sharing_ban._panel_unban,
                           {"status": "DISABLED", "description": "a", "tag": "VIP"}, "n")
        self.assertNotIn("tag", p[0][2])
        self.assertIn("VIP", res["tag_note"])

    def test_unban_no_tag_no_field(self):
        _, p = self._run(sharing_ban._panel_unban,
                         {"status": "DISABLED", "description": "a", "tag": None}, "n")
        self.assertNotIn("tag", p[0][2])

    def test_already_disabled_changes_nothing(self):
        calls = []

        def fake(path, method="GET", body=None):
            calls.append(method)
            return {"id": 7, "status": "DISABLED"}

        with mock.patch.object(sharing_ban, "rw_api", fake):
            res = sharing_ban._panel_ban(7, "NOTE")
        self.assertEqual(calls, ["GET"])
        self.assertTrue(res["skipped"])

    def test_unban_enables(self):
        calls = []

        def fake(path, method="GET", body=None):
            calls.append((method, path))
            return {"id": 7, "status": "DISABLED", "description": "a"}

        with mock.patch.object(sharing_ban, "rw_api", fake):
            res = sharing_ban._panel_unban(7, "разбанен")
        self.assertTrue(res["ok"])
        self.assertIn(("POST", "/api/users/7/actions/enable"), calls)


if __name__ == "__main__":
    unittest.main()
