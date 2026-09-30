"""Тесты следилки за нодами: порог, переходы, напоминания, пачки, «пропало большинство».

Сети, базы и Телеграма здесь нет. Время подаётся в tick() явно, опросы —
готовыми наблюдениям, а база подменена парой словарей: состояние следилки и
«таблица» node_outages. Перезапуск рагента — это новый Runtime поверх той же
«базы».
"""
import copy
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from app.services import node_watch as nw

T0 = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)   # 03:00 МСК
POLL = timedelta(seconds=30)


def ids(n: int) -> list[str]:
    return [str(uuid.UUID(int=i + 1)) for i in range(n)]


class Harness(unittest.IsolatedAsyncioTestCase):
    """Парк из нескольких нод и опросы раз в полминуты."""

    NODES = 10

    async def asyncSetUp(self) -> None:
        nw._runtime = nw.Runtime()
        self.cfg = {**nw.DEFAULTS, "utc_offset": 3}   # в сообщениях ждём московское время
        self.ids = ids(self.NODES)
        self.names = {nid: f"N{i + 1}" for i, nid in enumerate(self.ids)}
        self.hosting = {nid: "Alpha" if i < self.NODES // 2 else "Beta"
                        for i, nid in enumerate(self.ids)}
        self.muted: set[str] = set()
        self.gone: set[str] = set()
        self.ssh_alive: set[str] = set()      # агент молчит, а SSH открывается
        self.no_agent: set[str] = set()       # нод без агента
        self.state: dict = {}                 # app_settings[node_watch_state]
        self.rows: dict[uuid.UUID, dict] = {}  # «таблица» node_outages
        self.sent: list[str] = []
        self.send_error: str | None = None
        self.bot = True
        self.now = T0

        async def load(_db):
            watch = nw.Watch.load(copy.deepcopy(self.state))
            outages = {r["o"].node_id: copy.deepcopy(r["o"]) for r in self.rows.values()
                       if r["kind"] == nw.NODE and r["ended"] is None}
            return watch, outages

        async def apply(_db, plan, watch, _before) -> None:
            for o in plan.opened:
                self.rows[o.id] = {"kind": nw.NODE, "o": copy.deepcopy(o), "ended": None,
                                   "resolution": None}
            for o, ended, resolution in plan.closed:
                self.rows[o.id].update(ended=ended, resolution=resolution)
            if plan.fleet_opened is not None:
                self.rows[uuid.uuid4()] = {"kind": nw.FLEET, "o": None, "ended": None,
                                           "resolution": None,
                                           "since": plan.fleet_opened.since}
            if plan.fleet_closed is not None:
                for r in self.rows.values():
                    if r["kind"] == nw.FLEET and r["ended"] is None:
                        r.update(ended=plan.fleet_closed, resolution=nw.RECOVERED)
            # Через JSON, как в настоящей базе: несериализуемое тут и всплывёт.
            self.state = json.loads(json.dumps(watch.dump()))

        async def delivered(_db, when, notified, reminded, watch) -> None:
            for rid in notified:
                self.rows[rid]["o"].notified_at = when
            for rid in reminded:
                self.rows[rid]["o"].reminded_at = when
            if watch is not None:
                self.state = json.loads(json.dumps(watch.dump()))

        async def send(_db, _cfg, text):
            if self.send_error:
                return self.send_error
            self.sent.append(text)
            return None

        async def can_notify(_db, cfg):
            return bool(cfg.get("notify")) and self.bot

        for name, fake in (("_db_load", load), ("_db_apply", apply),
                           ("_db_delivered", delivered), ("_send", send),
                           ("_can_notify", can_notify)):
            mock.patch.object(nw, name, fake).start()
        self.addCleanup(mock.patch.stopall)

    # — помощники —

    def sightings(self, silent: set[str]) -> list[nw.Sighting]:
        out = []
        for nid in self.ids:
            if nid in self.gone:
                continue
            down = nid in silent
            agent = None if nid in self.no_agent else not down
            ssh = None if agent else (nid in self.ssh_alive or (agent is None and not down))
            out.append(nw.Sighting(
                node_id=nid, name=self.names[nid], host=f"10.0.0.{self.ids.index(nid) + 1}",
                hosting=self.hosting[nid], agent=agent, ssh=ssh, panel=not down,
                muted=nid in self.muted))
        return out

    async def poll(self, silent=()) -> nw.Plan:
        """Один опрос в текущий момент; часы после него уходят на полминуты вперёд."""
        plan = await nw.tick(None, self.cfg, self.sightings(set(silent)), self.now)
        self.now += POLL
        return plan

    async def run_for(self, minutes: float, silent=()) -> None:
        for _ in range(int(minutes * 2)):
            await self.poll(silent)

    async def warm_up(self) -> None:
        """Первый опрос после старта: всё отвечает."""
        await self.poll()

    def open_rows(self, kind: str = nw.NODE) -> list[dict]:
        return [r for r in self.rows.values() if r["kind"] == kind and r["ended"] is None]

    def restart(self) -> None:
        nw._runtime = nw.Runtime()


class ThresholdTests(Harness):
    async def test_single_missed_poll_is_not_an_alarm(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        await self.poll({a})
        await self.run_for(30)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows, {})
        self.assertEqual(self.state.get("pending"), {})

    async def test_silence_shorter_than_threshold_stays_quiet(self) -> None:
        await self.warm_up()
        await self.run_for(9.5, {self.ids[0]})
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows, {})

    async def test_one_message_when_silence_outlasts_threshold(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        await self.run_for(60, {a})
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0]
        self.assertIn("Нода недоступна", text)
        self.assertIn("N1", text)
        self.assertIn("10.0.0.1", text)
        self.assertIn("Alpha", text)
        # Первый неудачный опрос был в 00:00:30 UTC — в сообщении местное время.
        self.assertIn("с 03:00 МСК", text)
        self.assertIn("Агент молчит · SSH молчит · панель не видит", text)
        self.assertEqual(len(self.open_rows()), 1)

    async def test_alarm_fires_at_the_threshold_not_later(self) -> None:
        await self.warm_up()
        await self.run_for(10, {self.ids[0]})       # опросы 00:00:30 … 00:10:00
        self.assertEqual(self.sent, [])
        await self.poll({self.ids[0]})              # 00:10:30 — ровно десять минут молчания
        self.assertEqual(len(self.sent), 1)

    async def test_threshold_is_a_setting(self) -> None:
        self.cfg["threshold_min"] = 3
        await self.warm_up()
        await self.run_for(3.5, {self.ids[0]})
        self.assertEqual(len(self.sent), 1)

    async def test_interrupted_silence_starts_the_count_again(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        await self.run_for(8, {a})
        await self.poll()                 # один ответ — и счёт с нуля
        await self.run_for(8, {a})
        self.assertEqual(self.sent, [])


class RecoveryTests(Harness):
    async def test_one_message_on_recovery_with_downtime_and_start(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        await self.run_for(11, {a})
        self.assertEqual(len(self.sent), 1)
        # Лежит дальше: 7 часов 12 минут с начала молчания.
        self.cfg["remind_hours"] = 0
        self.now = T0 + POLL + timedelta(hours=7, minutes=12)
        await self.poll()
        self.assertEqual(len(self.sent), 2)
        text = self.sent[1]
        self.assertIn("Нода снова отвечает", text)
        self.assertIn("N1", text)
        self.assertIn("Лежала 7 ч 12 мин", text)
        self.assertIn("с 03:00 до 10:12 МСК", text)
        self.assertEqual(self.open_rows(), [])
        row = next(iter(self.rows.values()))
        self.assertEqual(row["resolution"], nw.RECOVERED)

        await self.run_for(30)
        self.assertEqual(len(self.sent), 2)   # и больше ни слова

    async def test_several_recoveries_come_as_one_message(self) -> None:
        await self.warm_up()
        trio = set(self.ids[:3])
        await self.run_for(12, trio)
        self.sent.clear()
        await self.poll()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Снова отвечают: 3 ноды", self.sent[0])


class ReminderTests(Harness):
    async def test_reminds_once_per_interval_while_down(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        await self.run_for(11, {a})
        self.assertEqual(len(self.sent), 1)
        await self.run_for(5 * 60, {a})                 # пять часов тишины в чате
        self.assertEqual(len(self.sent), 1)
        await self.run_for(70, {a})                     # шесть часов прошло
        self.assertEqual(len(self.sent), 2)
        self.assertIn("Всё ещё не отвечают: 1 нода", self.sent[1])
        self.assertIn("уже 6 ч", self.sent[1])
        await self.run_for(5 * 60, {a})
        self.assertEqual(len(self.sent), 2)
        await self.run_for(70, {a})
        self.assertEqual(len(self.sent), 3)

    async def test_zero_interval_means_no_reminders(self) -> None:
        self.cfg["remind_hours"] = 0
        await self.warm_up()
        await self.run_for(24 * 60, {self.ids[0]})
        self.assertEqual(len(self.sent), 1)

    async def test_reminder_lists_everything_still_down_at_once(self) -> None:
        await self.warm_up()
        a, b = self.ids[0], self.ids[1]
        await self.run_for(11, {a})
        await self.run_for(3 * 60, {a, b})     # вторая нода легла на три часа позже
        self.assertEqual(len(self.sent), 2)
        await self.run_for(3 * 60 + 5, {a, b})
        self.assertEqual(len(self.sent), 3)
        self.assertIn("Всё ещё не отвечают: 2 ноды", self.sent[2])
        # Напоминания сошлись: про вторую отдельного не будет ещё шесть часов.
        await self.run_for(4 * 60, {a, b})
        self.assertEqual(len(self.sent), 3)


class GroupTests(Harness):
    async def test_nodes_lost_in_one_poll_come_as_one_message(self) -> None:
        await self.warm_up()
        trio = set(self.ids[:3])
        await self.run_for(30, trio)
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0]
        self.assertIn("Недоступны: 3 ноды", text)
        for name in ("N1", "N2", "N3"):
            self.assertIn(name, text)
        self.assertEqual(len(self.open_rows()), 3)

    async def test_stragglers_a_poll_later_join_the_same_message(self) -> None:
        await self.warm_up()
        a, b, c = self.ids[:3]
        await self.poll({a})
        await self.poll({a, b})
        await self.poll({a, b, c})
        await self.run_for(30, {a, b, c})
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Недоступны: 3 ноды", self.sent[0])

    async def test_a_node_lost_much_later_gets_its_own_message(self) -> None:
        await self.warm_up()
        a, b = self.ids[:2]
        await self.run_for(5, {a})
        await self.run_for(30, {a, b})
        self.assertEqual(len(self.sent), 2)
        self.assertIn("N1", self.sent[0])
        self.assertNotIn("N2", self.sent[0])
        self.assertIn("N2", self.sent[1])


class FleetTests(Harness):
    async def test_majority_gone_is_one_message_about_the_agent_itself(self) -> None:
        await self.warm_up()
        lost = set(self.ids[:7])          # 5 у Alpha и 2 у Beta
        await self.run_for(60, lost)
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0]
        self.assertIn("Рагент не видит 7 из 10 нод", text)
        self.assertIn("проблема на стороне рагента", text)
        self.assertIn("Alpha — 5 из 5", text)
        self.assertIn("Beta — 2 из 5", text)
        self.assertEqual(self.open_rows(), [])              # по нодам тревог нет
        self.assertEqual(len(self.open_rows(nw.FLEET)), 1)

    async def test_short_blindness_says_nothing(self) -> None:
        await self.warm_up()
        await self.run_for(5, set(self.ids))
        await self.run_for(30)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows, {})

    async def test_sight_restored_is_one_message_and_nodes_get_a_fresh_count(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        await self.run_for(20, set(self.ids))
        self.assertEqual(len(self.sent), 1)
        await self.poll({a})               # парк вернулся, одна нода ещё молчит
        self.assertEqual(len(self.sent), 2)
        text = self.sent[1]
        self.assertIn("Рагент снова видит ноды", text)
        self.assertIn("Отвечают 9 из 10", text)
        self.assertIn("Ещё молчит: N1. Если не ответит за 10 мин", text)
        self.assertEqual(self.open_rows(nw.FLEET), [])

        # То, что рагент «видел» вслепую, не в счёт: порог для неё — заново.
        await self.run_for(9, {a})
        self.assertEqual(len(self.sent), 2)
        await self.run_for(2, {a})
        self.assertEqual(len(self.sent), 3)
        self.assertIn("Нода недоступна", self.sent[2])
        self.assertIn("с 03:00 МСК", self.sent[2])   # а молчит она всё равно с самого начала

    async def test_one_poll_right_after_blindness_is_not_an_alarm(self) -> None:
        await self.warm_up()
        await self.run_for(20, set(self.ids))
        await self.poll({self.ids[0]})     # парк вернулся, одна нода ещё не успела
        await self.run_for(30)
        self.assertEqual(len(self.sent), 2)           # «ослеп» и «прозрел», по ноде — ничего
        self.assertEqual(self.open_rows(), [])

    async def test_one_hoster_gone_is_named(self) -> None:
        # У Alpha шесть нод из десяти — и легли ровно они.
        self.hosting = {nid: "Alpha" if i < 6 else "Beta" for i, nid in enumerate(self.ids)}
        await self.warm_up()
        await self.run_for(15, set(self.ids[:6]))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Молчат только ноды Alpha", self.sent[0])

    async def test_already_down_nodes_do_not_count_towards_majority(self) -> None:
        await self.warm_up()
        first = set(self.ids[:3])
        await self.run_for(15, first)
        self.assertEqual(len(self.sent), 1)
        # Ещё три из оставшихся семи — это меньше половины живых: обычная тревога.
        second = first | set(self.ids[3:6])
        await self.run_for(15, second)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("Недоступны: 3 ноды", self.sent[1])
        self.assertEqual(self.open_rows(nw.FLEET), [])

    async def test_blindness_is_reminded_too(self) -> None:
        await self.warm_up()
        await self.run_for(6 * 60 + 20, set(self.ids))
        self.assertEqual(len(self.sent), 2)
        self.assertIn("всё ещё не видит 10 из 10", self.sent[1])


class SmallFleetTests(Harness):
    NODES = 3

    async def test_majority_rule_needs_a_fleet(self) -> None:
        """В парке из трёх нод «большинство» — это две ноды: тревожим по нодам."""
        await self.warm_up()
        await self.run_for(15, set(self.ids[:2]))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Недоступны: 2 ноды", self.sent[0])


class RestartTests(Harness):
    async def test_restart_does_not_repeat_the_alarm_or_invent_a_recovery(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        await self.run_for(15, {a})
        self.assertEqual(len(self.sent), 1)
        self.restart()
        await self.run_for(60, {a})
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(len(self.open_rows()), 1)

    async def test_recovery_after_restart_still_knows_when_it_went_down(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        await self.run_for(15, {a})
        self.restart()
        self.now = T0 + POLL + timedelta(hours=2)
        await self.poll()
        self.assertEqual(len(self.sent), 2)
        self.assertIn("Лежала 2 ч", self.sent[1])
        self.assertIn("с 03:00", self.sent[1])

    async def test_first_poll_after_start_is_not_a_failure(self) -> None:
        # Рагент только поднялся, сеть ещё не встала: не ответил никто.
        await self.poll(set(self.ids))
        self.assertEqual(self.state.get("pending"), {})
        self.assertNotIn("fleet", self.state)
        await self.run_for(30)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows, {})

    async def test_restart_gives_silent_nodes_a_fresh_count(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        await self.run_for(8, {a})
        self.restart()
        await self.run_for(9, {a})        # 17 минут молчания, но рагент видел из них девять
        self.assertEqual(self.sent, [])
        await self.run_for(2, {a})
        self.assertEqual(len(self.sent), 1)
        self.assertIn("с 03:00 МСК", self.sent[0])

    async def test_long_pause_between_polls_counts_as_restart(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        await self.run_for(8, {a})
        self.now += timedelta(hours=1)    # рагент час стоял
        await self.poll({a})
        self.assertEqual(self.sent, [])


class ExclusionTests(Harness):
    async def test_excluded_node_never_alarms(self) -> None:
        a = self.ids[0]
        self.muted.add(a)
        await self.warm_up()
        await self.run_for(60, {a})
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows, {})

    async def test_excluding_a_down_node_closes_the_alarm_silently(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        await self.run_for(15, {a})
        self.assertEqual(len(self.sent), 1)
        self.muted.add(a)
        await self.run_for(10, {a})
        self.assertEqual(len(self.sent), 1)
        row = next(iter(self.rows.values()))
        self.assertEqual(row["resolution"], nw.MUTED)
        # Нода поднялась, пока была исключена: «восстановилась» не шлём.
        await self.run_for(10)
        self.assertEqual(len(self.sent), 1)

    async def test_deleted_node_closes_the_alarm_silently(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        await self.run_for(15, {a})
        self.gone.add(a)
        await self.run_for(10)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(next(iter(self.rows.values()))["resolution"], nw.REMOVED)

    async def test_excluded_nodes_do_not_count_towards_majority(self) -> None:
        self.muted.update(self.ids[:6])
        await self.warm_up()
        await self.run_for(15, set(self.ids[:6]))
        self.assertEqual(self.sent, [])


class SignalTests(Harness):
    async def test_server_alive_with_silent_agent_is_a_different_message(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        self.ssh_alive.add(a)
        await self.run_for(12, {a})
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Нода жива, но агент не отдаёт данные", self.sent[0])
        self.assertIn("Агент молчит · SSH отвечает", self.sent[0])

    async def test_node_without_agent_is_judged_by_ssh(self) -> None:
        a = self.ids[0]
        self.no_agent.add(a)
        await self.warm_up()
        await self.run_for(30)
        self.assertEqual(self.sent, [])       # агента нет, SSH открыт — нода жива
        await self.run_for(12, {a})
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Агента нет · SSH молчит", self.sent[0])


class DeliveryTests(Harness):
    async def test_undelivered_alarm_is_retried_but_not_every_poll(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        self.send_error = "Telegram недоступен"
        attempts = 0

        async def failing(_db, _cfg, _text):
            nonlocal attempts
            attempts += 1
            return self.send_error

        with mock.patch.object(nw, "_send", failing):
            await self.run_for(10 + 14, {a})
        # За четырнадцать минут после порога — три попытки, а не двадцать восемь.
        self.assertEqual(attempts, 3)
        self.assertEqual(len(self.open_rows()), 1)
        self.assertIsNone(self.open_rows()[0]["o"].notified_at)

        self.send_error = None
        await self.run_for(6, {a})
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Нода недоступна", self.sent[0])
        await self.run_for(30, {a})
        self.assertEqual(len(self.sent), 1)

    async def test_undelivered_recovery_is_sent_later(self) -> None:
        await self.warm_up()
        a = self.ids[0]
        await self.run_for(12, {a})
        self.send_error = "Telegram недоступен"
        await self.poll()
        self.assertEqual(len(self.sent), 1)
        self.send_error = None
        await self.run_for(6)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("Нода снова отвечает", self.sent[1])

    async def test_notifications_off_keeps_the_journal_and_stays_silent(self) -> None:
        self.cfg["notify"] = False
        await self.warm_up()
        a = self.ids[0]
        await self.run_for(15, {a})
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.open_rows()), 1)
        # Включили, а нода всё ещё лежит — говорим о ней сразу.
        self.cfg["notify"] = True
        await self.poll({a})
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Нода недоступна", self.sent[0])


class ObserveTests(unittest.IsolatedAsyncioTestCase):
    """Вход от сэмплера: из ответов агентов и панели собираются наблюдения."""

    async def asyncSetUp(self) -> None:
        nw._runtime = nw.Runtime()
        self.cfg = dict(nw.DEFAULTS)
        self.ids = ids(4)
        self.nodes = [
            SimpleNamespace(id=uuid.UUID(nid), name=f"N{i + 1}", host=f"10.0.0.{i + 1}",
                            ssh_port=22 if i else 2222,
                            hosting=SimpleNamespace(name="Alpha") if i < 2 else None)
            for i, nid in enumerate(self.ids)
        ]
        self.seen: list[nw.Sighting] = []
        self.knocked: list[tuple[str, int]] = []
        self.ssh_open: set[str] = set()
        self.stood_down = 0

        async def get_config(_db):
            return self.cfg

        async def tick(_db, _cfg, sightings, _now=None):
            self.seen = sightings
            return nw.Plan()

        async def knock(host, port):
            self.knocked.append((host, port))
            return host in self.ssh_open

        async def prune(_db):
            return 0

        async def stand_down(_db):
            self.stood_down += 1
            nw._runtime = nw.Runtime(idle=True)

        for name, fake in (("get_config", get_config), ("tick", tick), ("prune", prune),
                           ("stand_down", stand_down), ("_knock", knock)):
            mock.patch.object(nw, name, fake).start()
        self.addCleanup(mock.patch.stopall)

    def agents(self, silent=()) -> dict:
        return {nid: (nid not in silent, None) for nid in self.ids}

    async def test_ssh_is_knocked_only_where_the_agent_is_silent(self) -> None:
        a, b = self.ids[0], self.ids[1]
        self.ssh_open.add("10.0.0.2")
        await nw.observe(None, self.nodes, self.agents({a, b}), {})
        self.assertEqual(sorted(self.knocked), [("10.0.0.1", 2222), ("10.0.0.2", 22)])
        by_id = {s.node_id: s for s in self.seen}
        self.assertIs(by_id[a].ssh, False)
        self.assertIs(by_id[b].ssh, True)
        self.assertIsNone(by_id[self.ids[2]].ssh)
        self.assertEqual(by_id[a].hosting, "Alpha")
        self.assertIsNone(by_id[self.ids[2]].hosting)

    async def test_node_without_agent_lives_by_its_ssh(self) -> None:
        a = self.ids[0]
        agents = self.agents()
        agents[a] = (None, None)
        self.ssh_open.add("10.0.0.1")
        await nw.observe(None, self.nodes, agents, {})
        s = next(x for x in self.seen if x.node_id == a)
        self.assertIsNone(s.agent)
        self.assertTrue(s.alive)

    async def test_healthy_agentless_node_is_not_knocked_on_every_poll(self) -> None:
        a = self.ids[0]
        agents = self.agents()
        agents[a] = (None, None)
        self.ssh_open.add("10.0.0.1")
        start = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
        with mock.patch.object(nw, "_now") as clock:
            for i in range(9):                      # четыре минуты опросов
                clock.return_value = start + i * POLL
                await nw.observe(None, self.nodes, agents, {})
                self.assertTrue(next(x for x in self.seen if x.node_id == a).alive)
            self.assertEqual(len(self.knocked), 3)  # 0:00, 2:00, 4:00
            # Порт закрылся. Заметим это со следующим стуком (в 6:00) — и дальше
            # стучимся уже на каждом опросе, чтобы не проспать возвращение.
            self.ssh_open.clear()
            for i in range(9, 16):
                clock.return_value = start + i * POLL
                await nw.observe(None, self.nodes, agents, {})
        self.assertFalse(next(x for x in self.seen if x.node_id == a).alive)
        self.assertEqual(len(self.knocked), 3 + 4)  # 6:00, 6:30, 7:00, 7:30

    async def test_excluded_and_panel_disabled_nodes_are_muted_and_left_alone(self) -> None:
        a, b, c = self.ids[:3]
        self.cfg["exclude"] = [a]
        panel = {"10.0.0.2": {"users": 0, "connected": False, "disabled": True},
                 "10.0.0.3": {"users": 4, "connected": True, "disabled": False}}
        await nw.observe(None, self.nodes, self.agents({a, b}), panel)
        by_id = {s.node_id: s for s in self.seen}
        self.assertTrue(by_id[a].muted)
        self.assertTrue(by_id[b].muted)
        self.assertFalse(by_id[c].muted)
        self.assertIs(by_id[c].panel, True)
        self.assertEqual(self.knocked, [])        # к исключённым и не стучимся

    async def test_panel_disabled_can_be_told_to_alarm_anyway(self) -> None:
        b = self.ids[1]
        self.cfg["skip_panel_disabled"] = False
        panel = {"10.0.0.2": {"users": 0, "connected": False, "disabled": True}}
        await nw.observe(None, self.nodes, self.agents({b}), panel)
        self.assertFalse(next(x for x in self.seen if x.node_id == b).muted)

    async def test_silent_panel_does_not_unmute_a_parked_node(self) -> None:
        b = self.ids[1]
        panel = {"10.0.0.2": {"users": 0, "connected": False, "disabled": True}}
        await nw.observe(None, self.nodes, self.agents({b}), panel)
        await nw.observe(None, self.nodes, self.agents({b}), {})     # панель не ответила
        self.assertTrue(next(x for x in self.seen if x.node_id == b).muted)

    async def test_switched_off_watcher_stands_down_once_and_stays_out(self) -> None:
        self.cfg["enabled"] = False
        await nw.observe(None, self.nodes, self.agents(set(self.ids)), {})
        await nw.observe(None, self.nodes, self.agents(set(self.ids)), {})
        self.assertEqual(self.stood_down, 1)
        self.assertEqual(self.seen, [])
        self.assertEqual(self.knocked, [])


class TextTests(unittest.TestCase):
    NOW = datetime(2026, 9, 30, 7, 24, tzinfo=timezone.utc)

    def outage(self, **kw) -> nw.Outage:
        base = dict(node_id=ids(1)[0], name="DE-1", host="203.0.113.7", hosting="Hetzner",
                    since=self.NOW - timedelta(minutes=10), alerted_at=self.NOW,
                    signals={"agent": False, "ssh": False, "panel": False})
        return nw.Outage(**{**base, **kw})

    def test_time_is_local_and_date_appears_only_for_other_days(self) -> None:
        clock = nw.Clock(3, self.NOW)
        self.assertEqual(clock.label, "МСК")
        self.assertEqual(clock.at(self.NOW), "10:24")
        self.assertEqual(clock.at(self.NOW - timedelta(hours=12)), "29.09 22:24")
        self.assertEqual(nw.Clock(5, self.NOW).label, "UTC+5")
        self.assertEqual(nw.Clock("мусор", self.NOW).label, "UTC+5")   # пояс по умолчанию
        self.assertEqual(nw.Clock(0, self.NOW).label, "UTC+0")

    def test_span_reads_like_a_human_wrote_it(self) -> None:
        self.assertEqual(nw.span(timedelta(seconds=20)), "1 мин")
        self.assertEqual(nw.span(timedelta(minutes=42)), "42 мин")
        self.assertEqual(nw.span(timedelta(hours=7, minutes=12)), "7 ч 12 мин")
        self.assertEqual(nw.span(timedelta(hours=40)), "1 сут 16 ч")

    def test_down_message_has_name_ip_hoster_time_and_signals(self) -> None:
        text = nw.down_text([self.outage()], self.NOW, nw.Clock(3, self.NOW))
        self.assertEqual(text, "\n".join([
            "🔴 <b>Нода недоступна</b>",
            "<b>DE-1</b> · 203.0.113.7 · Hetzner",
            "Не отвечает с 10:14 МСК — уже 10 мин.",
            "Агент молчит · SSH молчит · панель не видит",
        ]))

    def test_unknown_hoster_and_panel_are_simply_left_out(self) -> None:
        o = self.outage(hosting=None, signals={"agent": False, "ssh": False, "panel": None})
        text = nw.down_text([o], self.NOW, nw.Clock(3, self.NOW))
        self.assertIn("<b>DE-1</b> · 203.0.113.7\n", text)
        self.assertTrue(text.endswith("Агент молчит · SSH молчит"))

    def test_token_mismatch_is_named(self) -> None:
        sig = {"agent": False, "agent_error": "token", "ssh": True, "panel": True}
        self.assertEqual(nw.signals_text(sig),
                         "агент не принимает токен · SSH отвечает · панель видит")

    def test_names_are_escaped(self) -> None:
        text = nw.down_text([self.outage(name="<b>x</b>")], self.NOW, nw.Clock(3, self.NOW))
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", text)

    def test_long_lists_are_cut(self) -> None:
        many = [self.outage(node_id=nid, name=f"N{i}") for i, nid in enumerate(ids(40))]
        text = nw.down_text(many, self.NOW, nw.Clock(3, self.NOW))
        self.assertIn("и ещё 10", text)
        self.assertLess(len(text), 4096)

    def test_plurals(self) -> None:
        self.assertEqual(nw._nodes(1), "1 нода")
        self.assertEqual(nw._nodes(3), "3 ноды")
        self.assertEqual(nw._nodes(5), "5 нод")
        self.assertEqual(nw._nodes(11), "11 нод")
        self.assertEqual(nw._nodes(21), "21 нода")


class StateTests(unittest.TestCase):
    def test_state_survives_a_round_trip(self) -> None:
        watch = nw.Watch(
            pending={"a": T0},
            fleet=nw.Fleet(since=T0, down=7, total=10, by_hosting={"Alpha": [5, 5]},
                           alerted_at=T0 + timedelta(minutes=10)))
        again = nw.Watch.load(json.loads(json.dumps(watch.dump())))
        self.assertEqual(again.pending, {"a": T0})
        self.assertEqual(again.fleet.down, 7)
        self.assertEqual(again.fleet.alerted_at, T0 + timedelta(minutes=10))
        self.assertIsNone(again.fleet.notified_at)

    def test_garbage_state_is_an_empty_state(self) -> None:
        for junk in (None, [], {"pending": {"a": "не дата"}, "fleet": {"since": None}}):
            watch = nw.Watch.load(junk)
            self.assertEqual(watch.pending, {})
            self.assertIsNone(watch.fleet)


if __name__ == "__main__":
    unittest.main()
