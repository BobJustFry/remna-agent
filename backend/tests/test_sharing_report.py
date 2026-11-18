"""Отчёт-доказательство о шаринге: сборка из фиксированных данных, без сети и базы."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.services import ip_geo, sharing_ban, sharing_report as sr

NOW = datetime(2026, 10, 18, 17, 45, tzinfo=timezone.utc)

IZH = {"city": "Ижевск", "lat": 56.85, "lon": 53.20}
SPB = {"city": "Санкт-Петербург", "lat": 59.93, "lon": 30.33}
ULY = {"city": "Ульяновск", "lat": 54.31, "lon": 48.39}


def g(asn, isp, place=None, mobile=False, hosting=False):
    return {"asn": asn, "isp": isp, "mobile": mobile, "hosting": hosting, **(place or {})}


GEO = {
    "92.39.220.145": g(39001, "Newtone", IZH),
    "92.39.223.246": g(39001, "Newtone", IZH),          # второй адрес Newtone в Ижевске
    "176.212.230.73": g(34590, "ЭР-Телеком", IZH),
    "87.250.16.193": g(3226, "MARK-ITT", IZH),
    "37.113.80.155": g(34590, "ЭР-Телеком", SPB),
    "95.26.111.239": g(8402, "Corbina", ULY),
    "91.78.87.124": g(8359, "МТС", None, mobile=True),
    "94.77.9.218": g(31213, "Т2", None, mobile=True),
}


def at(hms: str) -> datetime:
    h, m, s = map(int, hms.split(":"))
    return NOW.replace(hour=h, minute=m, second=s)


def sess(ip, hms, node="al-wc"):
    return {"ip": ip, "seen": at(hms), "server": node}


CARD = {
    "id": 5159, "username": "XabibaAaj", "telegramId": 2037151494, "status": "ACTIVE",
    "createdAt": "2026-10-18T00:47:00Z", "hwidDeviceLimit": 3,
    "userTraffic": {"firstConnectedAt": "2026-10-18T00:49:00Z",
                    "lifetimeUsedTrafficBytes": int(169.16 * 1024 ** 3)},
}
DEVICES = [
    {"platform": "Windows", "deviceModel": "X99E", "userAgent": "Happ 4.1.3",
     "requestIp": "92.39.220.145", "updatedAt": "2026-10-18T16:54:00Z"},
]
SUBS = [{"requestIp": "92.39.220.145"}] * 24


def data(sessions, geo=None, card=None, **kw):
    return sr.ReportData(now=NOW, card=card or CARD, devices=DEVICES, sub_records=SUBS,
                         sessions=sessions, geo=GEO if geo is None else geo, **kw)


# Пример 1: 5 пар «провайдер + город» за минуту при лимите 3.
FIVE = [
    sess("87.250.16.193", "17:41:00"),
    sess("176.212.230.73", "17:41:20"),
    sess("92.39.220.145", "17:41:40"),
    sess("37.113.80.155", "17:42:00"),
    sess("95.26.111.239", "17:42:20"),
    sess("91.78.87.124", "17:40:55"),           # мобильный: не в счёт
]
# Пример 2: 4 пары.
FOUR = [
    sess("92.39.220.145", "17:41:00"),
    sess("176.212.230.73", "17:41:20"),
    sess("87.250.16.193", "17:41:40"),
    sess("95.26.111.239", "17:42:00"),
]
THREE = FOUR[:3]


def with_limit(n):
    return {**CARD, "hwidDeviceLimit": n}


class HardEvidenceTest(unittest.TestCase):
    def test_five_pairs_over_limit_three_is_hard(self):
        an = sr.analyse(data(FIVE))
        self.assertEqual(an.burst.combos, 5)
        self.assertEqual(an.level, sr.LEVEL_HARD)

    def test_four_pairs_over_limit_three_is_hard(self):
        an = sr.analyse(data(FOUR))
        self.assertEqual(an.burst.combos, 4)
        self.assertEqual(an.level, sr.LEVEL_HARD)

    def test_three_pairs_at_limit_three_is_not_proof(self):
        an = sr.analyse(data(THREE))
        self.assertEqual(an.burst.combos, 3)
        self.assertEqual(an.level, sr.LEVEL_INDIRECT)

    def test_same_provider_and_city_is_one_pair(self):
        s = [sess("92.39.220.145", "17:41:00"), sess("92.39.223.246", "17:41:10"),
             sess("176.212.230.73", "17:41:20"), sess("87.250.16.193", "17:41:30")]
        an = sr.analyse(data(s))
        self.assertEqual(len(an.wired), 4)
        self.assertEqual(an.burst.combos, 3)
        self.assertEqual(an.level, sr.LEVEL_INDIRECT)

    def test_same_provider_other_city_is_another_pair(self):
        s = [sess("176.212.230.73", "17:41:00"), sess("37.113.80.155", "17:41:10")]
        self.assertEqual(sr.analyse(data(s, card=with_limit(1))).level, sr.LEVEL_HARD)

    def test_spread_beyond_two_minutes_is_not_proof(self):
        s = [sess("87.250.16.193", "17:35:00"), sess("176.212.230.73", "17:37:30"),
             sess("92.39.220.145", "17:40:00"), sess("37.113.80.155", "17:42:30")]
        an = sr.analyse(data(s))
        self.assertLess(an.burst.combos, 4)
        self.assertEqual(an.level, sr.LEVEL_INDIRECT)

    def test_unlimited_is_never_proof(self):
        for lim in (0, None):
            an = sr.analyse(data(FIVE, card=with_limit(lim)))
            self.assertEqual(an.level, sr.LEVEL_INDIRECT, lim)
            self.assertIsNone(an.limit)

    def test_unknown_provider_or_city_not_counted(self):
        ips = ["1.1.1.1", "1.1.1.2", "1.1.1.3", "1.1.1.4"]
        geo = {ip: {"mobile": False} for ip in ips}       # ни провайдера, ни города
        s = [sess(ip, f"17:41:{i * 10:02d}") for i, ip in enumerate(ips)]
        an = sr.analyse(data(s, geo))
        self.assertEqual(an.burst.combos, 0)
        self.assertEqual(an.level, sr.LEVEL_INDIRECT)

    def test_cities_are_extra_fact_only(self):
        # Три города, но три пары при лимите 3 — доказательством не становятся.
        s = [sess("92.39.220.145", "17:41:49"), sess("37.113.80.155", "17:42:32"),
             sess("95.26.111.239", "17:42:49")]
        an = sr.analyse(data(s))
        self.assertEqual(an.level, sr.LEVEL_INDIRECT)
        self.assertGreaterEqual(len(an.pairs), 2)
        self.assertGreater(max(p.km for p in an.pairs), 1000)


class NotEvidenceTest(unittest.TestCase):
    def test_mobile_only_is_indirect_and_not_counted(self):
        s = [sess("91.78.87.124", "17:41:00"), sess("94.77.9.218", "17:41:10")]
        an = sr.analyse(data(s, card=with_limit(1)))
        self.assertEqual(an.level, sr.LEVEL_INDIRECT)
        self.assertEqual(len(an.wired), 0)
        self.assertEqual(len(an.mobile), 2)

    def test_same_city_is_not_a_city_pair(self):
        an = sr.analyse(data([sess("92.39.220.145", "17:41:00"), sess("87.250.16.193", "17:41:10")]))
        self.assertEqual(an.pairs, [])

    def test_near_cities_below_threshold_are_not_a_pair(self):
        geo = {"1.1.1.1": {"mobile": False, "city": "Ижевск", "lat": 56.85, "lon": 53.20},
               "1.1.1.2": {"mobile": False, "city": "Завьялово", "lat": 56.79, "lon": 53.37}}
        an = sr.analyse(data([sess("1.1.1.1", "17:41:00"), sess("1.1.1.2", "17:41:05")], geo))
        self.assertEqual(an.pairs, [])

    def test_old_sessions_outside_window_are_dropped(self):
        old = {"ip": "92.39.220.145", "seen": NOW - timedelta(hours=2), "server": "сервер 1"}
        self.assertEqual(sr.current_addrs(data([old])), [])


class TextTest(unittest.TestCase):
    def setUp(self):
        self.hard = sr.build_report(data(FIVE))
        self.weak = sr.build_report(data([sess("92.39.220.145", "17:41:49"),
                                          sess("91.78.87.124", "17:40:55")]))

    def test_account_facts(self):
        for needle in ("XabibaAaj / 5159", "2037151494", "18.10.2026 00:47 UTC (03:47 МСК)",
                       "Лимит устройств | 3", "169,16 ГБ", "ГБ/час", "Happ 4.1.3",
                       "Запросов в истории: 24, уникальных адресов: 1"):
            self.assertIn(needle, self.hard)

    def test_tables_have_provider_city_and_time(self):
        self.assertIn("| 37.113.80.155 | ЭР-Телеком (AS34590) | Санкт-Петербург |", self.hard)
        self.assertIn("17:42:00", self.hard)
        self.assertIn("МТС (AS8359)", self.hard)

    def test_hard_level_and_rule_text(self):
        self.assertIn("**жёсткое доказательство**", self.hard)
        self.assertIn("**5** различных пар", self.hard)
        self.assertIn("лимит устройств 3", self.hard)

    def test_no_impossible_in_two_places_wording(self):
        low = self.hard.lower()
        for bad in ("нужная скорость", "не может находиться", "не может быть", "физически"):
            self.assertNotIn(bad, low)
        self.assertIn("дополнительный факт", low)

    def test_weak_report_is_honest(self):
        self.assertIn("**косвенные признаки**", self.weak)
        self.assertIn("лимит не превышен", self.weak)
        self.assertNotIn("жёсткое доказательство**", self.weak)
        self.assertIn("Мобильные адреса как доказательство не учитываются", self.weak)

    def test_unknown_is_not_invented(self):
        txt = sr.build_report(data([sess("8.8.8.8", "17:41:00")], geo={}))
        self.assertIn("не определено", txt)
        self.assertIn("Проводных адресов в окне нет", txt)

    def test_no_infrastructure_names(self):
        s = [sess("92.39.220.145", "17:41:49", node="сервер 1")]
        txt = sr.build_report(data(s))
        for bad in ("al-wc", "uk-L4"):
            self.assertNotIn(bad, txt)

    def test_server_labels_hide_node_names(self):
        labels = sr._server_labels([{"node": "uk-L4"}, {"node": "al-wc"}, {"node": "uk-L4"}])
        self.assertEqual(sorted(labels.values()), ["сервер 1", "сервер 2"])
        self.assertNotIn("uk-L4", "".join(labels.values()))

    def test_banned_decision_line(self):
        txt = sr.build_report(data(FIVE, banned_at=NOW))
        self.assertIn("Решение: аккаунт отключён", txt)
        self.assertNotIn("Решение:", self.hard)

    def test_moscow_time(self):
        self.assertEqual(sr.fmt_dt(NOW), "18.10.2026 17:45 UTC (20:45 МСК)")


class ClientTextTest(unittest.TestCase):
    def setUp(self):
        self.txt = sr.build_client_text(data(FIVE, banned_at=NOW, score=92))
        self.weak = sr.build_client_text(data(THREE, banned_at=NOW, score=88))

    def test_no_jargon_or_internal_names(self):
        for t in (self.txt, self.weak):
            for bad in ("ASN", "AS39001", "AS34590", "HWID", "hwid", "NAT", "al-wc", "uk-L4",
                        "сервер 1", "UTC", "**", "|---"):
                self.assertNotIn(bad, t)

    def test_plain_lines_time_city_provider_address(self):
        self.assertIn("• 20:42:00 — Санкт-Петербург — ЭР-Телеком — 37.113.80.155", self.txt)
        self.assertIn("• 20:41:00 — Ижевск — MARK-ITT — 87.250.16.193", self.txt)
        self.assertNotIn("91.78.87.124", self.txt)          # мобильные в списке не показываем

    def test_main_argument_and_limits(self):
        self.assertIn("Разрешено устройств: 3", self.txt)
        self.assertIn("подключений (5) больше, чем разрешённых устройств (3)", self.txt)
        self.assertIn("мобильных сетей: 1", self.txt)

    def test_extra_facts_and_decision(self):
        self.assertIn("Список серверов обновляли 24 раз с 1 адреса(ов)", self.txt)
        self.assertIn("169,16 ГБ", self.txt)
        self.assertIn("ГБ в час", self.txt)
        self.assertIn("Решение: аккаунт отключён 18.10.2026 20:45 МСК", self.txt)

    def test_indirect_does_not_claim_excess(self):
        self.assertNotIn("Главный довод", self.weak)
        self.assertIn("Однозначно подтвердить превышение", self.weak)

    def test_unlimited_says_so(self):
        t = sr.build_client_text(data(FIVE, card=with_limit(0)))
        self.assertIn("не ограничен", t)
        self.assertNotIn("Главный довод", t)

    def test_no_two_places_argument(self):
        low = self.txt.lower()
        self.assertNotIn("в двух местах", low)
        self.assertNotIn("в разных городах одновременно", low)


class SplitTest(unittest.TestCase):
    def test_short_text_single_part_without_number(self):
        self.assertEqual(sr.split_parts("a\n\nb"), ["a\n\nb"])

    def test_splits_on_paragraphs_and_numbers(self):
        paras = [f"абзац {i} " + "я" * 900 for i in range(10)]
        parts = sr.split_parts("\n\n".join(paras))
        self.assertGreater(len(parts), 2)
        n = len(parts)
        for i, p in enumerate(parts, 1):
            self.assertLessEqual(len(p), sr.TG_LIMIT_HARD)
            self.assertTrue(p.startswith(f"{i}/{n}\n\n"))
        body = "\n\n".join(p.split("\n\n", 1)[1] for p in parts)
        self.assertEqual(body, "\n\n".join(paras))      # порядок и целые абзацы

    def test_paragraph_not_cut_when_it_fits(self):
        paras = ["х" * 3000, "у" * 3000]
        parts = sr.split_parts("\n\n".join(paras))
        self.assertEqual(len(parts), 2)
        self.assertIn("х" * 3000, parts[0])
        self.assertIn("у" * 3000, parts[1])

    def test_oversized_paragraph_split_by_lines(self):
        para = "\n".join(f"• строка {i} " + "z" * 100 for i in range(80))
        parts = sr.split_parts(para)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(len(p), sr.TG_LIMIT_HARD)
        self.assertEqual("\n".join(p.split("\n\n", 1)[1] for p in parts).count("• строка"), 80)

    def test_oversized_single_line_hard_cut(self):
        parts = sr.split_parts("w" * 9000)
        for p in parts:
            self.assertLessEqual(len(p), sr.TG_LIMIT_HARD)
        self.assertEqual(sum(len(p.split("\n\n", 1)[1]) for p in parts), 9000)

    def test_real_report_fits(self):
        for p in sr.split_parts(sr.build_client_text(data(FIVE, banned_at=NOW))):
            self.assertLessEqual(len(p), sr.TG_LIMIT_HARD)


class SendTest(unittest.TestCase):
    HIT = {"user_id": 5159, "username": "XabibaAaj", "score": 92}

    def run_send(self, *, token="tok", chat="123", notify=True, send=None, text=None):
        sent: list[tuple] = []

        def fake_send(tok, ch, msg, parse_mode="HTML"):
            sent.append((ch, msg, parse_mode))
            return send(len(sent)) if send else None

        async def bot_token(db):
            return token

        async def eff_chat(db, cfg):
            return chat

        with mock.patch.object(sharing_ban.subwatch, "bot_token", bot_token), \
             mock.patch.object(sharing_ban.sharing_watch, "effective_chat", eff_chat), \
             mock.patch.object(sharing_ban, "send_message", fake_send):
            ok = asyncio.run(sharing_ban.send_client_report(
                None, {"notify": notify}, self.HIT, True,
                text if text is not None else sr.build_client_text(data(FIVE, banned_at=NOW))))
        return ok, sent

    def test_header_first_then_plain_text_parts(self):
        ok, sent = self.run_send()
        self.assertTrue(ok)
        self.assertGreaterEqual(len(sent), 2)
        self.assertTrue(all(pm is None for _, _, pm in sent))     # без разметки
        self.assertIn("XabibaAaj", sent[0][1])
        self.assertIn("Балл: 92 из 100", sent[0][1])
        self.assertIn("автоматически", sent[0][1])
        self.assertIn("Отключён аккаунт XabibaAaj", sent[1][1])

    def test_multi_part_sent_in_order_numbered(self):
        big = "\n\n".join(f"абзац {i} " + "я" * 1500 for i in range(6))
        ok, sent = self.run_send(text=big)
        self.assertTrue(ok)
        msgs = [m for _, m, _ in sent[1:]]
        n = len(msgs)
        self.assertGreater(n, 1)
        for i, m in enumerate(msgs, 1):
            self.assertTrue(m.startswith(f"{i}/{n}\n"))
        self.assertIn(f"сообщений: {n}", sent[0][1])

    def test_bot_not_ready_is_silent(self):
        for kw in ({"token": ""}, {"chat": ""}):
            ok, sent = self.run_send(**kw)
            self.assertFalse(ok)
            self.assertEqual(sent, [])

    def test_send_failure_stops_but_does_not_raise(self):
        ok, sent = self.run_send(send=lambda n: "сеть недоступна" if n == 2 else None)
        self.assertFalse(ok)
        self.assertEqual(len(sent), 2)

    def test_unexpected_exception_swallowed(self):
        def boom(n):
            raise RuntimeError("x")
        ok, _ = self.run_send(send=boom)
        self.assertFalse(ok)


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeDb:
    def begin_nested(self):
        return _Nested()


class BanSurvivesSendFailureTest(unittest.TestCase):
    def test_ban_ok_even_if_report_send_raises(self):
        report = sr.BanReport("md", "client", "u")
        stored: list = []

        async def prepare(db, uid, now):
            return report

        async def store(db, uid, text, username, now, client=""):
            stored.append((text, client))

        async def append_log(db, row):
            return None

        async def notify(*a, **k):
            return None

        async def boom(*a, **k):
            raise RuntimeError("telegram down")

        with mock.patch.object(sharing_ban.sharing_report, "prepare_for_ban", prepare), \
             mock.patch.object(sharing_ban.sharing_report, "store", store), \
             mock.patch.object(sharing_ban, "_panel_ban", lambda uid, note: {"ok": True, "steps": ["disable"]}), \
             mock.patch.object(sharing_ban, "_append_log", append_log), \
             mock.patch.object(sharing_ban, "_notify_text", notify), \
             mock.patch.object(sharing_ban.subwatch, "bot_token", boom):
            res = asyncio.run(sharing_ban.ban(FakeDb(), {"notify": True},
                                              {"user_id": 7, "username": "u", "score": 90}, "auto"))
        self.assertTrue(res["ok"])
        self.assertEqual(stored, [("md", "client")])        # копия сохранена до отправки

    def test_no_send_when_ban_skipped(self):
        calls: list = []

        async def prepare(db, uid, now):
            return sr.BanReport("md", "client", "u")

        async def store(*a, **k):
            return None

        async def append_log(db, row):
            return None

        async def send(*a, **k):
            calls.append(1)

        with mock.patch.object(sharing_ban.sharing_report, "prepare_for_ban", prepare), \
             mock.patch.object(sharing_ban.sharing_report, "store", store), \
             mock.patch.object(sharing_ban, "_panel_ban",
                               lambda uid, note: {"ok": True, "skipped": "уже отключён"}), \
             mock.patch.object(sharing_ban, "_append_log", append_log), \
             mock.patch.object(sharing_ban, "send_client_report", send):
            asyncio.run(sharing_ban.ban(FakeDb(), {"notify": True},
                                        {"user_id": 7, "username": "u", "score": 90}, "manual"))
        self.assertEqual(calls, [])


class HelpersTest(unittest.TestCase):
    def test_distance_izhevsk_spb(self):
        km = sr.distance_km(IZH, SPB)
        self.assertTrue(1300 < km < 1600, km)

    def test_distance_without_coords(self):
        self.assertIsNone(sr.distance_km({"city": "x"}, SPB))

    def test_filename_is_ascii(self):
        self.assertEqual(sr.report_filename(5, "Ха/би ba", NOW), "sharing-5-ba-20261018.md")
        self.assertEqual(sr.report_filename(5, "", NOW), "sharing-5-user-20261018.md")

    def test_parse_geo_item(self):
        item = {"status": "success", "query": "1.2.3.4", "as": "AS39001 Newtone", "isp": "Newtone",
                "city": "Ижевск", "regionName": "Удмуртия", "countryCode": "RU",
                "lat": 56.85, "lon": 53.2, "mobile": False, "hosting": False}
        r = ip_geo.parse_item(item)
        self.assertEqual((r["asn"], r["city"], r["mobile"], r["ok"]), (39001, "Ижевск", False, True))

    def test_parse_geo_failure_and_missing(self):
        self.assertEqual(ip_geo.parse_item({"status": "fail", "query": "1.2.3.4"}),
                         {"ip": "1.2.3.4", "ok": False})
        self.assertIsNone(ip_geo.parse_item({"status": "fail"}))
        r = ip_geo.parse_item({"status": "success", "query": "1.2.3.4"})
        self.assertIsNone(r["mobile"])
        self.assertIsNone(r["city"])

    def test_private_ip_not_looked_up(self):
        self.assertFalse(ip_geo._public("10.0.0.1"))
        self.assertTrue(ip_geo._public("92.39.220.145"))


if __name__ == "__main__":
    unittest.main()
