"""Тесты сторожа доноров ИИ: реестр из профилей, здоровье, решения, антифлап,
фаервол, сравнение-и-замена при PATCH, сбои панели и переключения, рестарт посреди."""
import asyncio
import contextlib
import copy
import json
import socket
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.services import donor_probe as dp
from app.services import donor_watch as dw
from app.services import donor_watch_runner as runner
from app.services import subwatch

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
FI2 = "151.241.229.200"
OLD = "194.180.189.133"
PARIS = "151.247.196.156"
DEYA = "95.179.166.217"


def sock(tag, addr, user="u1", pw="p1", port=10443):
    return {"tag": tag, "protocol": "socks",
            "settings": {"servers": [{"address": addr, "port": port, "users": [{"user": user, "pass": pw}]}]}}


CLAUDE_RULE = ["domain:claude.ai", "domain:anthropic.com"]
GEMINI_RULE = ["full:ai.google.dev", "full:alkalimakersuite-pa.clients6.google.com"]
ELEVEN_RULE = ["domain:elevenlabs.io"]
TT_RULE = ["domain:tiktok.com", "domain:tiktokv.com"]


def profile(uuid, name, outbounds, rules, inbounds=None):
    return {"uuid": uuid, "name": name, "config": {
        "inbounds": inbounds or [], "outbounds": [{"tag": "DIRECT", "protocol": "freedom"}, *outbounds],
        "routing": {"rules": [{"outboundTag": t, "domain": d} for t, d in rules]}}}


def node(name, addr, prof, connected=True, uuid=None, uptime=1000):
    return {"name": name, "address": addr, "uuid": uuid or f"n-{name}", "isConnected": connected,
            "isDisabled": False, "xrayUptime": uptime,
            "configProfile": {"activeConfigProfileUuid": prof, "activeInbounds": []}}


def fleet():
    profiles = [
        profile("p-cz", "CZ", [sock("ai-gemini", PARIS), sock("ai-claude", FI2)],
                [("ai-gemini", GEMINI_RULE), ("ai-gemini", ELEVEN_RULE), ("ai-claude", CLAUDE_RULE)]),
        profile("p-se", "SE", [sock("ai-gemini", OLD), sock("ai-claude", OLD)],
                [("ai-gemini", GEMINI_RULE), ("ai-claude", CLAUDE_RULE)]),
        profile("p-de", "DE", [sock("ai-gemini", DEYA, "u2", "p2"), sock("ai-claude", FI2)],
                [("ai-gemini", TT_RULE), ("ai-claude", CLAUDE_RULE)]),
        profile("p-fi", "FI", [sock("ai-claude", OLD)], [("ai-claude", CLAUDE_RULE)],
                inbounds=[{"tag": "AI-CASCADE-IN", "protocol": "mixed", "port": 10443,
                           "settings": {"accounts": [{"user": "u1", "pass": "p1"}]}}]),
        profile("p-ya", "YA-DE", [], [], inbounds=[{"tag": "AI-CASCADE-IN", "port": 10443,
                                                    "settings": {"accounts": [{"user": "u2", "pass": "p2"}]}}]),
    ]
    nodes = [node("cz-1", "195.123.229.164", "p-cz"), node("se-1", "10.0.0.5", "p-se"),
             node("de-1", "193.247.77.253", "p-de"), node("fi-2", FI2, "p-fi"), node("fi-AI", "95.81.79.45", "p-fi"),
             node("de-cdn-yandex", DEYA, "p-ya"), node("fi-cdn-yandex", "37.27.20.242", "p-x")]
    return profiles, nodes


def cfg(**kw):
    return dw.clean_config({**kw})


def reg_and_state(**kw):
    profiles, nodes = fleet()
    c = cfg(**kw)
    reg = dw.discover(profiles, nodes, c)
    st = {"donors": {}, "bindings": {}, "switches": [], "patches": [], "alerts": {}}
    dw.sync_bindings(reg, st, NOW)
    return reg, st, c


def mark(st, key, svc, health, at=NOW, streak=1, last_dead=None):
    s = st.setdefault("donors", {}).setdefault(key, {"svc": {}})["svc"].setdefault(svc, {})
    s.update({"health": health, "at": dw._iso(at), "alive_streak": streak if health == dw.ALIVE else 0})
    if last_dead:
        s["last_dead_at"] = dw._iso(last_dead)


def alive_all(st, key, svcs=dw.SERVICES, streak=1):
    for s in svcs:
        mark(st, key, s, dw.ALIVE, streak=streak)


K = lambda a: f"{a}:10443"  # noqa: E731


class ClassifyTest(unittest.TestCase):
    def test_domains(self):
        self.assertEqual(dw.classify_domain("domain:claude.ai"), "claude")
        self.assertEqual(dw.classify_domain("full:alkalimakersuite-pa.clients6.google.com"), "gemini")
        self.assertEqual(dw.classify_domain("domain:elevenlabs.cloud"), "elevenlabs")
        self.assertEqual(dw.classify_domain("domain:tiktokv.eu"), "tiktok")
        self.assertEqual(dw.classify_domain("geosite:openai"), "openai")
        self.assertIsNone(dw.classify_domain("domain:grok.com"))
        self.assertIsNone(dw.classify_domain("domain:notclaude.ai.example"))

    def test_interval_floor_20min(self):
        self.assertEqual(cfg(interval_min=5)["interval_min"], 20)
        self.assertTrue(cfg()["dry_run"])


class DiscoverTest(unittest.TestCase):
    def test_bindings_services_and_creds(self):
        reg, _st, _c = reg_and_state()
        b = {x.id: x for x in reg.bindings}
        self.assertEqual(b["p-cz:ai-gemini"].services, ["gemini", "elevenlabs"])
        self.assertEqual(b["p-de:ai-gemini"].services, ["tiktok"])
        self.assertEqual(b["p-cz:ai-claude"].node_names, ["cz-1"])
        # логины — только в памяти; в снимке для API их нет
        self.assertIn(K(FI2), reg.creds)
        snap = json.dumps(dw.registry_snapshot(reg))
        self.assertNotIn("p1", snap.replace("p-", ""))
        self.assertNotIn('"u1"', snap)

    def test_retired_and_inbound_donors(self):
        reg, st, _c = reg_and_state()
        self.assertTrue(reg.donors[K(OLD)].retired)
        self.assertEqual(reg.donors[K(OLD)].successor, K(FI2))
        self.assertTrue(any(i["code"] == "retired_in_use" for i in reg.issues))
        # донор с входа AI-CASCADE-IN — логин с входа идёт первым
        self.assertEqual(reg.creds[K(DEYA)][0].user, "u2")
        # привязка на списанный адрес считает родиной преемника
        self.assertEqual(st["bindings"]["p-se:ai-claude"]["home"], K(FI2))

    def test_ip_change_detected_from_panel(self):
        profiles, nodes = fleet()
        for n in nodes:
            if n["name"] == "de-cdn-yandex":
                n["address"] = "9.9.9.9"
        reg = dw.discover(profiles, nodes, cfg())
        self.assertTrue(any(i["code"] == "ip_changed" and DEYA in i["text"] for i in reg.issues))
        self.assertEqual(reg.donors[K(DEYA)].successor, K("9.9.9.9"))
        self.assertIn(K("9.9.9.9"), reg.creds)

    def test_probe_plan_skips_retired(self):
        reg, _st, c = reg_and_state()
        plan = dw.probe_plan(reg, c)
        self.assertNotIn(K(OLD), plan)
        self.assertIn("claude", plan[K(FI2)])
        self.assertIn("tiktok", plan[K(DEYA)])


class HealthTest(unittest.TestCase):
    def test_confirmation(self):
        self.assertEqual(dw.health_of(("timeout", ""), ("timeout", "")), dw.DEAD)
        self.assertEqual(dw.health_of(("timeout", ""), None), dw.UNKNOWN)
        self.assertEqual(dw.health_of(("timeout", ""), ("ok", "")), dw.ALIVE)
        self.assertEqual(dw.health_of(("challenge", ""), None), dw.ALIVE)    # челлендж CF = жив
        self.assertEqual(dw.health_of(("error", ""), None), dw.UNKNOWN)
        self.assertEqual(dw.health_of(("hop_failed", ""), None), dw.UNKNOWN)

    def test_flap_breaks_alive_streak(self):
        st = {}
        dw.update_streaks(st, {"k": {"claude": {"health": dw.ALIVE, "raw": "ok"}}}, NOW)
        dw.update_streaks(st, {"k": {"claude": {"health": dw.ALIVE, "raw": "ok"}}}, NOW)
        self.assertEqual(st["donors"]["k"]["svc"]["claude"]["alive_streak"], 2)
        dw.update_streaks(st, {"k": {"claude": {"health": dw.ALIVE, "raw": "ok", "flapped": True}}}, NOW)
        self.assertEqual(st["donors"]["k"]["svc"]["claude"]["alive_streak"], 0)


class DecideTest(unittest.TestCase):
    def setUp(self):
        self.reg, self.st, self.c = reg_and_state()
        self.at = dw._iso(NOW)

    def acts(self):
        return {a.binding: a for a in dw.decide(self.reg, self.st, self.c, NOW, self.at)}

    def test_all_alive_nothing(self):
        for k in (K(FI2), K(PARIS), K(DEYA)):
            alive_all(self.st, k)
        a = self.acts()
        self.assertNotIn("p-cz:ai-claude", a)
        self.assertNotIn("p-cz:ai-gemini", a)

    def test_unknown_never_switches(self):
        alive_all(self.st, K(PARIS))
        mark(self.st, K(FI2), "claude", dw.UNKNOWN)
        self.assertNotIn("p-cz:ai-claude", self.acts())

    def test_dead_primary_switches_to_alive_candidate_in_same_profile(self):
        mark(self.st, K(FI2), "claude", dw.DEAD)
        alive_all(self.st, K(PARIS))              # Париж уже в профиле CZ → allowlist доказан
        a = self.acts()["p-cz:ai-claude"]
        self.assertEqual((a.kind, a.dst), ("switch", K(PARIS)))

    def test_candidate_needs_all_services_alive(self):
        mark(self.st, K(PARIS), "gemini", dw.DEAD)
        mark(self.st, K(PARIS), "elevenlabs", dw.DEAD)
        alive_all(self.st, K(FI2), ["gemini"])   # ElevenLabs у fi-2 не подтверждён
        a = self.acts()["p-cz:ai-gemini"]
        self.assertEqual(a.kind, "no_candidate")

    def test_unverified_allowlist_blocks_foreign_donor(self):
        mark(self.st, K(FI2), "claude", dw.DEAD)
        alive_all(self.st, K(DEYA))               # de-cdn-yandex: профиль CZ на нём не бывал
        a = self.acts()["p-cz:ai-claude"]
        self.assertEqual(a.kind, "no_candidate")
        self.c["allow_unverified_allowlist"] = True
        self.assertEqual(self.acts()["p-cz:ai-claude"].dst, K(DEYA))

    def test_verified_allowlist(self):
        mark(self.st, K(FI2), "claude", dw.DEAD)
        alive_all(self.st, K(DEYA))
        self.st["donors"][K(DEYA)]["allowlist"] = {"ips": ["195.123.229.164/32"], "open": False}
        self.assertEqual(self.acts()["p-cz:ai-claude"].dst, K(DEYA))
        self.st["donors"][K(DEYA)]["allowlist"] = {"ips": ["1.1.1.1"], "open": False}
        self.assertEqual(self.acts()["p-cz:ai-claude"].kind, "no_candidate")

    def test_retired_goes_to_successor_and_no_self_loop(self):
        alive_all(self.st, K(FI2))
        self.st["donors"][K(FI2)]["allowlist"] = {"ips": ["10.0.0.5"], "open": False}
        a = self.acts()
        self.assertEqual((a["p-se:ai-claude"].kind, a["p-se:ai-claude"].dst), ("switch", K(FI2)))
        # FI: донор fi-2 стоит на нодах этого же профиля — петля, нельзя
        self.assertEqual(a["p-fi:ai-claude"].kind, "no_candidate")

    def test_quarantine_of_recently_dead(self):
        mark(self.st, K(FI2), "claude", dw.DEAD)
        mark(self.st, K(PARIS), "claude", dw.ALIVE, last_dead=NOW - timedelta(minutes=30))
        self.assertEqual(self.acts()["p-cz:ai-claude"].kind, "no_candidate")

    def test_switch_back_rules(self):
        b = self.st["bindings"]["p-cz:ai-claude"]
        b["current"] = K(PARIS)
        b["home"] = K(FI2)
        self.reg.bindings = [x for x in self.reg.bindings if x.id != "p-cz:ai-claude"] + [
            dw.Binding("p-cz:ai-claude", "p-cz", "CZ", "ai-claude", PARIS, 10443, "",
                       ["claude"], ["cz-1"], ["195.123.229.164"])]
        alive_all(self.st, K(PARIS))
        alive_all(self.st, K(FI2), ["claude"], streak=2)
        b["switched_at"] = dw._iso(NOW - timedelta(hours=2))
        self.assertNotIn("p-cz:ai-claude", self.acts())            # 2 цикла — мало
        alive_all(self.st, K(FI2), ["claude"], streak=3)
        self.assertEqual(self.acts()["p-cz:ai-claude"].kind, "switch_back")
        b["switched_at"] = dw._iso(NOW - timedelta(minutes=30))
        self.assertEqual(self.acts()["p-cz:ai-claude"].kind, "hold")  # не чаще раза в час

    def test_daily_patch_cap_and_binding_cap(self):
        mark(self.st, K(FI2), "claude", dw.DEAD)
        alive_all(self.st, K(PARIS))
        self.st["patches"] = [{"at": dw._iso(NOW - timedelta(hours=1))}] * self.c["max_patches_24h"]
        self.assertEqual(self.acts()["p-cz:ai-claude"].kind, "cap")
        self.st["patches"] = []
        self.st["switches"] = [{"at": dw._iso(NOW - timedelta(hours=1)), "binding": "p-cz:ai-claude"}] * 3
        self.assertEqual(self.acts()["p-cz:ai-claude"].kind, "cap")

    def test_halted(self):
        mark(self.st, K(FI2), "claude", dw.DEAD)
        alive_all(self.st, K(PARIS))
        self.st["halted"] = {"reason": "x"}
        self.assertEqual(self.acts()["p-cz:ai-claude"].kind, "halted")

    def test_stale_measurement_is_unknown(self):
        mark(self.st, K(FI2), "claude", dw.DEAD, at=NOW - timedelta(minutes=20))
        alive_all(self.st, K(PARIS))
        self.assertNotIn("p-cz:ai-claude", self.acts())

    def test_external_change_resets_home(self):
        self.st["bindings"]["p-cz:ai-claude"]["current"] = K(OLD)
        notes = dw.sync_bindings(self.reg, self.st, NOW)
        self.assertTrue(notes)
        self.assertEqual(self.st["bindings"]["p-cz:ai-claude"]["home"], K(FI2))


class ApplyChangesTest(unittest.TestCase):
    def test_cas_and_creds(self):
        profiles, _ = fleet()
        conf = profiles[0]["config"]
        new = dw.apply_changes(conf, [("ai-claude", FI2, 10443, PARIS, 10443, dp.Creds("x", "y"))])
        self.assertEqual(dw.outbound_target(new, "ai-claude"), K(PARIS))
        self.assertEqual(new["outbounds"][2]["settings"]["servers"][0]["users"], [{"user": "x", "pass": "y"}])
        self.assertEqual(new["outbounds"][1], conf["outbounds"][1])   # соседний аутбаунд не тронут
        self.assertEqual(dw.outbound_target(conf, "ai-claude"), K(FI2))  # исходник не изменён
        with self.assertRaises(dw.Conflict):
            dw.apply_changes(conf, [("ai-claude", OLD, 10443, PARIS, 10443, dp.Creds("x", "y"))])
        with self.assertRaises(dw.Conflict):
            dw.apply_changes(conf, [("nope", FI2, 10443, PARIS, 10443, dp.Creds("x", "y"))])


class FirewallTest(unittest.TestCase):
    def test_iptables_ufw_multiport_ipset(self):
        text = "\n".join([
            "*filter",
            "-A INPUT -s 193.247.77.253/32 -p tcp -m tcp --dport 10443 -j ACCEPT",
            "-A ufw-user-input -s 195.123.229.164/32 -p tcp -m multiport --dports 443,10443 -j ACCEPT",
            "-A INPUT -p tcp -m set --match-set aic src -m tcp --dport 10443 -j ACCEPT",
            "-A INPUT -p tcp -m tcp --dport 10443 -j DROP",
            "@@IPSET", "Name: aic", "Type: hash:ip", "Members:", "103.82.103.31", "",
            "@@NFT"])
        ips, open_all = dw.parse_firewall(text, 10443)
        self.assertFalse(open_all)
        self.assertEqual(ips, {"193.247.77.253/32", "195.123.229.164/32", "103.82.103.31"})
        self.assertTrue(dw.ip_allowed("195.123.229.164", ips))
        self.assertFalse(dw.ip_allowed("151.243.171.51", ips))

    def test_open_and_absent_and_nft(self):
        self.assertEqual(dw.parse_firewall("-A INPUT -p tcp --dport 10443 -j ACCEPT\n@@IPSET\n@@NFT", 10443),
                         (set(), True))
        self.assertEqual(dw.parse_firewall("-A INPUT -p tcp --dport 22 -j ACCEPT\n@@IPSET\n@@NFT", 10443),
                         (None, False))
        nft = ("@@IPSET\n@@NFT\ntable inet f {\n set ok {\n  type ipv4_addr\n  elements = { 1.2.3.4, 5.6.7.8 }\n }\n"
               " chain input {\n  tcp dport 10443 ip saddr @ok accept\n  tcp dport 10443 ip saddr { 9.9.9.9 } accept\n }\n}")
        ips, open_all = dw.parse_firewall(nft, 10443)
        self.assertEqual(ips, {"1.2.3.4", "5.6.7.8", "9.9.9.9"})
        self.assertFalse(open_all)

    def test_gaps_reported(self):
        reg, st, _c = reg_and_state()
        st["donors"][K(FI2)] = {"svc": {}, "allowlist": {"ips": ["193.247.77.253"], "open": False}}
        self.assertEqual(dw.allowlist_gaps(reg, st), {K(FI2): ["cz-1"]})

    def test_gaps_skip_node_that_is_the_donor(self):
        reg, st, _c = reg_and_state()
        st["donors"][K(FI2)] = {"svc": {}, "allowlist": {"ips": ["193.247.77.253"], "open": False}}
        for b in reg.bindings:
            if b.donor_key == K(FI2) and "fi-2" not in b.node_names:
                b.node_names.append("fi-2")
        gaps = dw.allowlist_gaps(reg, st)
        self.assertNotIn("fi-2", gaps[K(FI2)])
        self.assertIn("cz-1", gaps[K(FI2)])
        # по одному адресу, без имени ноды в реестре
        reg.donors[K(FI2)].node = None
        self.assertNotIn("fi-2", dw.allowlist_gaps(reg, st)[K(FI2)])
        # обычная нода fi-AI (не донор FI2) без allowlist — попадает
        for b in reg.bindings:
            if b.donor_key == K(FI2):
                b.node_names.append("fi-AI")
        self.assertIn("fi-AI", dw.allowlist_gaps(reg, st)[K(FI2)])


class SocksTest(unittest.TestCase):
    def _server(self, replies):
        a, b = socket.socketpair()

        def run():
            for want, reply in replies:
                b.recv(want)
                if reply is None:
                    return            # молчим
                b.sendall(reply)
        threading.Thread(target=run, daemon=True).start()
        return a, b

    def test_ok_auth_fail_silence(self):
        a, _b = self._server([(3, b"\x05\x02"), (64, b"\x01\x00")])
        self.assertEqual(dp.socks_auth(a, dp.Creds("u", "p"), 2), dp.RAW_OK)
        a, _b = self._server([(3, b"\x05\x02"), (64, b"\x01\x01")])
        self.assertEqual(dp.socks_auth(a, dp.Creds("u", "p"), 2), dp.RAW_AUTH)
        a, _b = self._server([(3, None)])
        self.assertEqual(dp.socks_auth(a, dp.Creds("u", "p"), 0.5), dp.RAW_TIMEOUT)

    def test_scrub(self):
        self.assertNotIn("sekret", dp.scrub("socks5h://usr:sekret@127.0.0.1:1 sekret", ["sekret"]))
        self.assertEqual(repr(dp.Creds("a", "b")), "Creds(***)")


class ElevenGeminiJudgeTest(unittest.TestCase):
    def test_eleven(self):
        r = dp.aa.Resp(code=302, url="https://elevenlabs.io/", headers={"location": "https://help.elevenlabs.io/x"})
        self.assertEqual(dp.judge_elevenlabs(r).verdict, "blocked")
        self.assertEqual(dp.judge_elevenlabs(dp.aa.Resp(code=404, url="u")).verdict, "ok")


class HopLogicTest(unittest.TestCase):
    """«Не достучаться» с одного непроверенного прыжка — не повод объявлять смерть."""

    def setUp(self):
        self.reg, self.st, self.c = reg_and_state()
        self.h1 = dp.Hop("de-2", "h1", 22, "root", "password", "x")
        self.h2 = dp.Hop("de-1", "h2", 22, "root", "password", "x")

    def run_probe(self, outcomes, hops):
        seq = iter(outcomes)

        def fake(hop, *a, **k):
            return {s: (next(seq), "") for s in ["claude"]}
        with mock.patch.object(dp, "probe", side_effect=fake):
            return asyncio.run(runner._probe_donor(self.reg, self.st, self.c, K(FI2), ["claude"], hops, ""))[0]

    def test_unproven_single_hop_unreachable_is_unknown(self):
        self.assertEqual(self.run_probe(["unreachable"], [self.h1])["claude"][0], "error")

    def test_second_hop_rescues(self):
        self.assertEqual(self.run_probe(["unreachable", "ok"], [self.h1, self.h2])["claude"][0], "ok")

    def test_two_hops_unreachable_is_fail(self):
        self.assertEqual(self.run_probe(["unreachable", "unreachable"], [self.h1, self.h2])["claude"][0],
                         "unreachable")

    def test_proven_hop_unreachable_is_fail(self):
        self.st["donors"][K(FI2)] = {"svc": {}, "good_hops": ["de-2"]}
        self.assertEqual(self.run_probe(["unreachable"], [self.h1])["claude"][0], "unreachable")

    def test_no_hop(self):
        self.assertEqual(self.run_probe([], [])["claude"][0], "no_hop")


class PostCheckTest(unittest.TestCase):
    def test_restart_kick_once_then_ok(self):
        before = {"n1": {"name": "cz-1", "connected": True, "disabled": False, "uptime": 5000}}
        snaps = iter([{"n1": {"name": "cz-1", "connected": True, "disabled": False, "uptime": 5015 + i * 15}}
                      for i in range(5)] + [{"n1": {"name": "cz-1", "connected": True, "disabled": False, "uptime": 10}}])
        clock = iter(range(0, 10000, 15))
        calls = []
        with mock.patch.object(runner, "_node_snapshot", side_effect=lambda u: next(snaps)), \
             mock.patch.object(runner.time, "monotonic", side_effect=lambda: next(clock)), \
             mock.patch("app.services.remnawave_api.rw_api", side_effect=lambda *a: calls.append(a)):
            ok, msg = runner._post_check("p", before, 180, sleep=lambda s: None)
        self.assertTrue(ok, msg)
        self.assertEqual(len(calls), 1)

    def test_node_not_back(self):
        before = {"n1": {"name": "cz-1", "connected": True, "disabled": False, "uptime": 5000}}
        clock = iter(range(0, 10000, 15))
        with mock.patch.object(runner, "_node_snapshot",
                               return_value={"n1": {"name": "cz-1", "connected": False, "disabled": False,
                                                    "uptime": None}}), \
             mock.patch.object(runner.time, "monotonic", side_effect=lambda: next(clock)):
            ok, msg = runner._post_check("p", before, 60, sleep=lambda s: None)
        self.assertFalse(ok)
        self.assertIn("cz-1", msg)


# ── цикл целиком на подделках ───────────────────────────────────────────────

class FakeDb:
    async def commit(self):
        pass

    async def execute(self, *_a, **_k):
        class R:
            def scalars(self):
                return self

            def all(self):
                return []
        return R()


class CycleBase(unittest.TestCase):
    def setUp(self):
        self.store: dict = {}
        self.patches: list = []
        self.profiles, self.nodes = fleet()
        self.panel_down = False
        self.patch_fails = False
        self.snapshot_fails = False
        self.probe_calls = 0
        self.health = {}       # ключ → сырой исход для всех сервисов

    def _snapshot(self, uuid):
        if self.snapshot_fails:
            raise RuntimeError('Remnawave API 500: {"pass":"leak"}')
        return {}

    async def _get(self, db, key, fallback):
        return {**fallback, **copy.deepcopy(self.store.get(key, {}))}

    async def _save(self, db, key, data):
        self.store[key] = copy.deepcopy(data)

    def _panel_read(self):
        if self.panel_down:
            raise RuntimeError("Remnawave API: timed out")
        return copy.deepcopy(self.nodes), copy.deepcopy(self.profiles)

    def _profile(self, uuid):
        return copy.deepcopy(next(p for p in self.profiles if p["uuid"] == uuid))

    def _rw_api(self, path, method="GET", body=None):
        if method == "PATCH":
            self.patches.append(body["uuid"])
            if self.patch_fails:
                raise RuntimeError("Remnawave API 500")
            for p in self.profiles:
                if p["uuid"] == body["uuid"]:
                    p["config"] = body["config"]
        return {}

    async def _probe(self, reg, st, cfg, key, services, hops, gemini_key):
        self.probe_calls += 1
        raw = self.health.get(key, "ok")
        return {s: (raw, "") for s in services}, None

    def _ctx(self):
        async def fake_sleep(s):
            pass
        stack = contextlib.ExitStack()
        for p in (mock.patch.object(subwatch, "_json_setting", self._get),
                  mock.patch.object(subwatch, "_save_json", self._save),
                  mock.patch.object(subwatch, "bot_token", mock.AsyncMock(return_value="")),
                  mock.patch.object(runner, "_panel_read", self._panel_read),
                  mock.patch.object(runner, "_panel_profile", self._profile),
                  mock.patch.object(runner, "_node_snapshot", self._snapshot),
                  mock.patch.object(runner, "_probe_donor", self._probe),
                  mock.patch.object(runner, "_hops_for", mock.AsyncMock(return_value=[])),
                  mock.patch.object(runner, "_check_allowlists", mock.AsyncMock()),
                  mock.patch.object(runner, "_retry", lambda fn, *a, **k: fn(*a)),
                  mock.patch("app.services.ai_access.gemini_key", mock.AsyncMock(return_value="")),
                  mock.patch("app.services.crypto.encrypt_secret", lambda s: "E:" + s),
                  mock.patch("app.services.crypto.decrypt_secret", lambda s: s[2:]),
                  mock.patch("app.services.remnawave_api.rw_api", self._rw_api),
                  mock.patch.object(runner.asyncio, "sleep", fake_sleep)):
            stack.enter_context(p)
        return stack

    def run_cycle(self, **cfgkw):
        self.store[dw.CONFIG_KEY] = {**cfgkw}
        with self._ctx():
            out = asyncio.run(runner.run_cycle(FakeDb()))
        return out, self.store.get(dw.STATE_KEY, {}), self.store.get(dw.LOG_KEY, {}).get("rows", [])

    def rows(self):
        return self.store.get(dw.LOG_KEY, {}).get("rows", [])


class CycleTest(CycleBase):
    def test_dry_run_never_patches(self):
        self.health[K(FI2)] = "timeout"
        out, st, rows = self.run_cycle()
        self.assertEqual(self.patches, [])
        self.assertTrue(any(a["kind"] == "switch" for a in out["actions"]))
        self.assertTrue(any(r["kind"] == "alert" and "сделал бы" in r["text"] for r in rows))
        self.assertTrue(out["rechecks"] > 0)

    def test_live_patches_one_profile_each_and_backs_up(self):
        self.health[K(FI2)] = "timeout"
        out, st, rows = self.run_cycle(dry_run=False)
        # CZ: fi-2 → Париж; DE: fi-2 → de-cdn-yandex (профиль DE уже на нём);
        # SE: списанный → преемник fi-2 мёртв, остальные доноры в SE не бывали → нет кандидата
        self.assertEqual(self.patches, ["p-cz", "p-de"])
        self.assertEqual(dw.outbound_target(self.profiles[2]["config"], "ai-claude"), K(DEYA))
        self.assertTrue(any(a["kind"] == "no_candidate" and a["binding"] == "p-se:ai-claude" for a in out["actions"]))
        self.assertEqual(dw.outbound_target(self.profiles[0]["config"], "ai-claude"), K(PARIS))
        self.assertIsNone(st.get("pending"))
        self.assertIsNone(st.get("halted"))
        self.assertEqual(st["bindings"]["p-cz:ai-claude"]["current"], K(PARIS))
        self.assertEqual(st["bindings"]["p-cz:ai-claude"]["home"], K(FI2))
        self.assertEqual(len(self.store[dw.BACKUP_KEY]["rows"]), 2)   # бэкап на каждый профиль

    def test_patch_failure_halts(self):
        self.health[K(FI2)] = "timeout"
        self.patch_fails = True
        _out, st, rows = self.run_cycle(dry_run=False)
        self.assertTrue(st.get("halted"))
        self.assertIsNone(st.get("pending"))
        self.assertTrue(any(r["kind"] == "patch_failed" for r in rows))
        # следующий цикл: остановлено — PATCH больше нет
        self.patch_fails = False
        self.patches.clear()
        self.store[dw.STATE_KEY]["last_cycle"] = None
        out, st, _ = self.run_cycle(dry_run=False)
        self.assertEqual(self.patches, [])
        self.assertTrue(any(a["kind"] == "halted" for a in out["actions"]))

    def test_panel_down_no_decisions(self):
        self.panel_down = True
        out, st, rows = self.run_cycle(dry_run=False)
        self.assertEqual(self.patches, [])
        self.assertIn("error", out)
        out, st, rows = self.run_cycle(dry_run=False)
        self.assertIn("panel", st["alerts"])

    def test_restart_mid_switch_resumes(self):
        # рагент упал после PATCH, до сверки: в панели уже новый адрес
        self.profiles[0]["config"] = dw.apply_changes(
            self.profiles[0]["config"], [("ai-claude", FI2, 10443, PARIS, 10443, dp.Creds("u1", "p1"))])
        self.store[dw.STATE_KEY] = {"bindings": {"p-cz:ai-claude": {"home": K(FI2), "current": K(FI2)}},
                                    "pending": {"profile_uuid": "p-cz", "profile_name": "CZ", "backup": "b",
                                                "changes": [{"binding": "p-cz:ai-claude", "tag": "ai-claude",
                                                             "src": K(FI2), "dst": K(PARIS)}]}}
        _out, st, rows = self.run_cycle(dry_run=False)
        self.assertIsNone(st.get("pending"))
        self.assertEqual(st["bindings"]["p-cz:ai-claude"]["current"], K(PARIS))
        self.assertEqual(st["bindings"]["p-cz:ai-claude"]["home"], K(FI2))   # не принято за чужую правку
        self.assertTrue(any(r["kind"] == "resume" for r in rows))

    def test_restart_before_patch_halts_safely(self):
        self.store[dw.STATE_KEY] = {"pending": {"profile_uuid": "p-cz", "profile_name": "CZ", "backup": "b",
                                                "changes": [{"binding": "p-cz:ai-claude", "tag": "ai-claude",
                                                             "src": K(FI2), "dst": K(PARIS)}]}}
        _out, st, _rows = self.run_cycle(dry_run=False)
        self.assertTrue(st.get("halted"))
        self.assertEqual(self.patches, [])

    def test_dry_run_one_summary_message(self):
        self.health[K(FI2)] = "timeout"
        self.store[dw.STATE_KEY] = {"alerts": {"plan:old": {"text": "x", "since": "", "notified_at": ""}}}
        _out, st, rows = self.run_cycle()
        plan_rows = [r for r in rows if "сделал бы" in r["text"]]
        self.assertEqual(len(plan_rows), 1)
        self.assertIn("(2)", plan_rows[0]["text"])            # CZ и DE одним сообщением
        self.assertNotIn("plan:old", st["alerts"])
        self.assertFalse(any(r["kind"] == "clear" for r in rows))   # старый ключ снят молча

    def test_order_fewest_online_first_and_skipped_order(self):
        self.health[K(FI2)] = "timeout"
        self.nodes[0]["usersOnline"] = 50     # cz-1
        self.nodes[2]["usersOnline"] = 5      # de-1
        self.patch_fails = True
        self.run_cycle(dry_run=False)
        self.assertEqual(self.patches, ["p-de"])             # DE первым, на сбое — стоп
        skipped = [r for r in self.rows() if r["kind"] == "skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertIn("p-cz:", skipped[0]["text"])
        self.assertNotIn("p-de:", skipped[0]["text"])

    def test_profile_without_nodes_not_switched(self):
        self.health[K(FI2)] = "timeout"
        self.profiles.append(profile("p-empty", "TR-RAW-REALITY-ITU", [sock("ai-claude", FI2)],
                                     [("ai-claude", CLAUDE_RULE)]))
        out, _st, _rows = self.run_cycle(dry_run=False)
        self.assertNotIn("p-empty", self.patches)
        self.assertFalse(any(a["binding"].startswith("p-empty:") for a in out["actions"]))

    def test_time_budget_defers_profiles(self):
        self.health[K(FI2)] = "timeout"
        out, st, _rows = self.run_cycle(dry_run=False, cycle_budget_s=300, post_check_s=300)
        self.assertEqual(self.patches, [])
        self.assertEqual(sorted(out["deferred"]), ["CZ", "DE"])
        self.assertIn("deferred", st["alerts"])
        self.assertIsNone(st.get("halted"))

    def test_node_snapshot_error_guarded(self):
        self.health[K(FI2)] = "timeout"
        self.snapshot_fails = True
        out, st, rows = self.run_cycle(dry_run=False)
        self.assertEqual(self.patches, [])
        self.assertIsNone(st.get("pending"))
        self.assertIsNone(st.get("halted"))
        self.assertEqual(st["last_cycle"]["at"], out["at"])
        err = [r for r in rows if r["kind"] == "error"]
        self.assertTrue(err and "PATCH не делаю" in err[0]["text"])
        self.assertNotIn("leak", json.dumps(rows, ensure_ascii=False))


class StormTest(CycleBase):
    """Цикл падает после проб — следующий раунд проб всё равно не раньше interval_min."""

    def setUp(self):
        super().setUp()
        self.clock = [NOW]
        self.mono = [1000.0]
        runner._fail_until = 0.0

    def tearDown(self):
        runner._fail_until = 0.0

    def tick(self, minutes, **cfgkw):
        self.clock[0] += timedelta(minutes=minutes)
        self.mono[0] += minutes * 60
        self.store[dw.CONFIG_KEY] = {**cfgkw}
        before = self.probe_calls
        with self._ctx(), mock.patch.object(runner, "_now", lambda: self.clock[0]), \
                mock.patch.object(dw, "_now", lambda: self.clock[0]), \
                mock.patch.object(runner.time, "monotonic", lambda: self.mono[0]):
            delay = asyncio.run(runner._tick(FakeDb()))
        return self.probe_calls > before, delay

    def test_throw_after_probes_no_retry_storm(self):
        self.health[K(FI2)] = "timeout"
        with mock.patch.object(dw, "decide", side_effect=RuntimeError("boom")):
            probed, delay = self.tick(0)
            self.assertTrue(probed)
            self.assertGreaterEqual(delay, 20 * 60)
            rounds = 0
            for _ in range(5):                       # раньше: заход каждые 60 с
                rounds += self.tick(1)[0]
            runner._fail_until = 0.0                 # рагент перезапустился: память пуста, база — нет
            for _ in range(5):
                rounds += self.tick(1)[0]
            self.assertEqual(rounds, 0)
            self.assertTrue(self.tick(15)[0])        # прошло 25 мин — следующий раунд

    def test_snapshot_error_then_minute_ticks(self):
        self.health[K(FI2)] = "timeout"
        self.snapshot_fails = True
        self.assertTrue(self.tick(0, dry_run=False)[0])
        self.assertEqual(sum(self.tick(1, dry_run=False)[0] for _ in range(10)), 0)
        self.assertEqual(self.patches, [])

    def test_pending_resolved_without_probes(self):
        self.store[dw.STATE_KEY] = {"cycle_started_at": dw._iso(NOW),
                                    "pending": {"profile_uuid": "p-cz", "profile_name": "CZ", "backup": "b",
                                                "changes": [{"binding": "p-cz:ai-claude", "tag": "ai-claude",
                                                             "src": K(FI2), "dst": K(PARIS)}]}}
        probed, delay = self.tick(1)
        self.assertFalse(probed)
        self.assertLessEqual(delay, 300)
        st = self.store[dw.STATE_KEY]
        self.assertIsNone(st.get("pending"))
        self.assertTrue(st.get("halted"))           # правки в панели нет — остановка


class RestoreTest(CycleBase):
    def restore(self, bid, dry_run=True):
        with self._ctx():
            return asyncio.run(runner.restore_backup(FakeDb(), bid, dry_run))

    def test_restore_preview_and_apply(self):
        self.health[K(FI2)] = "timeout"
        self.run_cycle(dry_run=False)
        rows = self.store[dw.BACKUP_KEY]["rows"]
        cz = next(r for r in rows if r["profile_uuid"] == "p-cz")
        self.patches.clear()
        prev = self.restore(cz["id"])
        self.assertFalse(prev["applied"])
        self.assertEqual(self.patches, [])
        self.assertEqual(prev["outbounds"], [{"tag": "ai-claude", "now": K(PARIS), "backup": K(FI2),
                                              "creds_change": False}])
        self.assertNotIn("p1", json.dumps(prev))      # логины не светим
        out = self.restore(cz["id"], dry_run=False)
        self.assertTrue(out["applied"])
        self.assertEqual(self.patches, ["p-cz"])
        self.assertEqual(dw.outbound_target(self.profiles[0]["config"], "ai-claude"), K(FI2))
        st = self.store[dw.STATE_KEY]
        self.assertEqual(st["bindings"]["p-cz:ai-claude"]["current"], K(FI2))
        self.assertEqual(len(self.store[dw.BACKUP_KEY]["rows"]), len(rows) + 1)   # бэкап «до отката»
        self.assertTrue(any(r["kind"] == "restore" for r in self.rows()))
        # повторно — уже совпадает, PATCH не нужен
        self.patches.clear()
        self.assertTrue(self.restore(cz["id"], dry_run=False)["identical"])
        self.assertEqual(self.patches, [])

    def test_restore_unknown_and_pending(self):
        with self.assertRaises(LookupError):
            self.restore("nope")
        self.health[K(FI2)] = "timeout"
        self.run_cycle(dry_run=False)
        bid = self.store[dw.BACKUP_KEY]["rows"][0]["id"]
        self.store[dw.STATE_KEY]["pending"] = {"profile_uuid": "p-cz"}
        with self.assertRaises(RuntimeError):
            self.restore(bid, dry_run=False)


class HopSelectTest(unittest.TestCase):
    def setUp(self):
        self.reg, self.st, self.c = reg_and_state()
        from types import SimpleNamespace as NS
        mk = lambda nm, ip: NS(name=nm, host=ip, ssh_port=22, ssh_user="root", auth_type="password",  # noqa: E731
                               password_enc="x", private_key_enc=None)
        self.by_ip = {"193.247.77.253": mk("de-1", "193.247.77.253"),
                      "195.123.229.164": mk("cz-1", "195.123.229.164")}

    def hops(self, **kw):
        c = cfg(probe_hops={"Париж": ["de-1"]}, **kw)
        with mock.patch("app.services.crypto.decrypt_secret", lambda s: s):
            return [h.name for h in asyncio.run(runner._hops_for(None, self.reg, self.st, c, K(PARIS), self.by_ip))]

    def test_exclude_and_only(self):
        self.assertEqual(self.hops(), ["de-1", "cz-1"])
        self.assertEqual(self.hops(probe_hops_exclude=["cz-1"]), ["de-1"])
        self.assertEqual(self.hops(probe_hops_only=True), ["de-1"])


class ScrubTest(unittest.TestCase):
    def test_panel_echo(self):
        out = dp.scrub('Remnawave API 400: {"user":"a","pass":"s3cr3t"} Authorization: Bearer tok123', [])
        self.assertNotIn("s3cr3t", out)
        self.assertNotIn("tok123", out)

    def test_budget_clamped_to_interval(self):
        self.assertLessEqual(cfg(cycle_budget_s=3600, interval_min=20)["cycle_budget_s"], 20 * 60 - 60)
        self.assertEqual(cfg()["max_patches_24h"], 24)


if __name__ == "__main__":
    unittest.main()
