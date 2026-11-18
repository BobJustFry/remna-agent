import asyncio
import copy
import unittest
from unittest import mock

from app.api import nodes
from app.schemas import NodeUpdate
from app.services import donor_watch_runner, mem_watch, metrics_sampler, monitoring_gate, subwatch


class MonitoringTransitionTest(unittest.IsolatedAsyncioTestCase):
    async def test_disable_waits_for_running_sampler(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def sample():
            entered.set()
            await release.wait()
            return 0

        with mock.patch.object(metrics_sampler, "_sample_once", side_effect=sample), \
             mock.patch.object(nodes, "_update_node", new_callable=mock.AsyncMock, return_value="saved") as save:
            running = asyncio.create_task(metrics_sampler.sample_once())
            await entered.wait()
            disable = asyncio.create_task(nodes.update_node("id", NodeUpdate(monitoring_enabled=False), "user", None))
            await asyncio.sleep(0)
            self.assertFalse(disable.done())
            save.assert_not_awaited()
            release.set()
            await running
            self.assertEqual(await disable, "saved")
            save.assert_awaited_once()

    async def test_paused_node_preserves_restart_safety_state(self):
        state = {"nodes": {"paused": {**mem_watch.new_node_state(),
                 "blocked": {"reason": "relapse"}, "pending": {"at": "2026-01-01T00:00:00Z"},
                 "last_restart_at": "2026-01-01T00:00:00Z", "alarm": {"critical": True}, "bad": 5}},
                 "restarts": []}
        saved = {}

        async def get_state(_db):
            return copy.deepcopy(state)

        async def save(_db, key, value):
            saved[key] = copy.deepcopy(value)

        db = mock.Mock()
        db.commit = mock.AsyncMock()
        with mock.patch.object(mem_watch, "get_state", side_effect=get_state), \
             mock.patch.object(subwatch, "_save_json", side_effect=save), \
             mock.patch.object(mem_watch, "_append_log", new_callable=mock.AsyncMock), \
             mock.patch.object(mem_watch, "_panel_restart") as restart:
            await mem_watch.tick(db, {**mem_watch.DEFAULTS, "enabled": True}, [],
                                 existing_ids={"paused"})
        paused = saved[mem_watch.STATE_KEY]["nodes"]["paused"]
        self.assertEqual(paused["blocked"], {"reason": "relapse"})
        self.assertIsNotNone(paused["pending"])
        self.assertEqual(paused["last_restart_at"], "2026-01-01T00:00:00Z")
        self.assertIsNone(paused["alarm"])
        self.assertEqual(paused["bad"], 0)
        restart.assert_not_called()

    def test_generation_changes_on_transition(self):
        previous = monitoring_gate.generation
        monitoring_gate.invalidate()
        self.assertEqual(monitoring_gate.generation, previous + 1)

    def test_donor_check_finishes_when_node_is_paused_during_restart(self):
        before = {
            "a": {"name": "a", "address": "a", "connected": True, "disabled": False, "uptime": 5000},
            "b": {"name": "b", "address": "b", "connected": True, "disabled": False, "uptime": 5000},
        }
        first = {"a": {"connected": True, "uptime": 5100}, "b": {"connected": False, "uptime": None}}
        second = {"b": {"connected": True, "uptime": 5}}
        clock = iter([0, 1, 75, 76, 90])
        calls = {"a": 0, "b": 0}

        def monitored(address):
            calls[address] += 1
            return address != "a" or calls[address] == 1

        with mock.patch.object(donor_watch_runner, "_node_snapshot", side_effect=[first, second]), \
             mock.patch.object(donor_watch_runner.time, "monotonic", side_effect=lambda: next(clock)):
            ok, _ = donor_watch_runner._post_check("p", before, 180, sleep=lambda _: None,
                                                   restart=lambda *_: False, monitored=monitored)
        self.assertTrue(ok)
