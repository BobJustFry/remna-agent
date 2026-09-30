"""Тесты проверки доступа к ИИ: вердикты, прогоны, очередь, отмена, матрица.

Тела ответов синтетические, но повторяют признаки настоящих из замера
29–30.09: страницу «Unable to load site», редирект Claude на
app-unavailable-in-region, челлендж Cloudflare, JSON-ошибки API, ответ
StreamGenerate с BardErrorInfo [1060]. Сеть и xray замоканы, база — тоже:
прогон пишет в неё через несколько функций `_db_*`, их и подменяем.
"""
import asyncio
import os
import socket
import sys
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from app.services import ai_access as ai
from app.services.ai_access import Resp

HTML = {"content-type": "text/html; charset=utf-8", "server": "cloudflare"}
JSON = {"content-type": "application/json", "server": "cloudflare"}

CHATGPT_OK = (
    "<!DOCTYPE html><html><head><title>ChatGPT: Chat, Work, Create &amp; Code with AI</title>"
    "<script>window.__x = 'unavailable in region is a string in js, not text';</script></head>"
    "<body><div>Search chats</div><div>New chat</div><div>Log in</div></body></html>"
)
CHATGPT_BLOCKED = (
    "<html><head><style>body{font-family:Arial}</style></head><body><div class='container'>"
    "<svg><path d='M1 1'/></svg><div class='message'>Unable to load site</div>"
    "<div class='explanation'>Please try again later. If you are using a VPN, try turning it off.</div>"
    "<div class='data'>[IP:203.0.113.7 | Ray ID:0000000000000000]</div></div></body></html>"
)
CF_CHALLENGE = (
    "<!DOCTYPE html><html lang='en-US'><head><title>Just a moment...</title>"
    "<meta http-equiv='content-security-policy' content=\"script-src https://challenges.cloudflare.com\">"
    "</head><body><script src='/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1'></script>"
    "</body></html>"
)
CLAUDE_LOGIN = "<!DOCTYPE html><html><head><title>Claude</title></head><body><div id='root'></div></body></html>"
CLAUDE_REGION = (
    "<html><head><title>App unavailable in region | Claude by Anthropic</title></head>"
    "<body><h1>App unavailable in region</h1></body></html>"
)
OPENAI_401 = ('{"error":{"message":"Missing bearer authentication in header",'
              '"type":"invalid_request_error","param":null,"code":null}}')
OPENAI_403 = ('{"error":{"code":"unsupported_country_region_territory","message":'
              '"Country, region, or territory not supported","param":null,"type":"request_forbidden"}}')
ANTHROPIC_401 = ('{"type":"error","error":{"type":"authentication_error",'
                 '"message":"x-api-key header is required"},"request_id":"req_test"}')
ANTHROPIC_403 = '{"error":{"type":"forbidden","message":"Request not allowed"}}'

GEMINI_PAGE = (
    "<html><head><title>\u200eGoogle Gemini</title></head><body><script>"
    'WIZ_global_data = {"cfb2h":"boq_assistant-bard-web-server_20260101.00_p0",'
    '"FdrFJe":"-1234567890","SNlM0e":"AFakeToken:1"};'
    'x=[1,2,1,200,"RUS",null];</script></body></html>'
)
GEMINI_OK = (
    ")]}'\n\n1415\n"
    '[["wrb.fr",null,"[null,[\\"c_1\\",\\"r_1\\"],null,null,[[\\"rc_abc\\",[\\"pong\\"],null]],'
    '[\\"Bulgaria\\",\\"SWML_DESCRIPTION_FROM_YOUR_INTERNET_ADDRESS\\",false],null,null,\\"BG\\"]"]]\n'
)
GEMINI_1060 = (
    ")]}'\n\n121\n"
    '[["wrb.fr",null,null,null,null,[9,null,[["type.googleapis.com/assistant.boq.bard.application.'
    'BardErrorInfo",[1060]]]]]]\n59\n[["di",1203],["af.httprm",1202,"-1",12]]\n'
)
GEMINI_OTHER_ERR = (
    ")]}'\n\n80\n"
    '[["wrb.fr",null,null,null,null,[7,null,[["type.googleapis.com/assistant.boq.bard.application.'
    'BardErrorInfo",[1037]]]]]]\n'
)
TRACE = "fl=1\nh=chatgpt.com\nip=104.28.0.1\nloc=PL\nwarp=on\ngateway=off\n"


class JudgeTests(unittest.TestCase):
    def test_chatgpt(self) -> None:
        self.assertEqual(ai.judge_chatgpt_web(Resp(200, "https://chatgpt.com/", CHATGPT_OK, HTML)).verdict, ai.OK)
        v = ai.judge_chatgpt_web(Resp(403, "https://chatgpt.com/", CHATGPT_BLOCKED, HTML))
        self.assertEqual((v.verdict, v.note), (ai.BLOCKED, "403 Unable to load site"))
        # Челлендж — отдельный статус, не отказ по стране.
        chal = Resp(403, "https://chatgpt.com/", CF_CHALLENGE, {**HTML, "cf-mitigated": "challenge"})
        self.assertEqual(ai.judge_chatgpt_web(chal).verdict, ai.CHALLENGE)
        # Отказ на языке выхода ловим маркером в видимом тексте.
        de = Resp(200, "https://chatgpt.com/", "<html><title>ChatGPT</title><body>"
                  "ChatGPT ist in Ihrem Land nicht verfügbar</body></html>", HTML)
        self.assertEqual(ai.judge_chatgpt_web(de).verdict, ai.BLOCKED)
        self.assertEqual(ai.judge_chatgpt_web(Resp(502, "https://chatgpt.com/", "bad gateway", HTML)).verdict,
                         ai.ERROR)

    def test_challenge_without_header(self) -> None:
        r = Resp(403, "https://chatgpt.com/", CF_CHALLENGE, HTML)
        self.assertTrue(ai.is_challenge(r))
        # Та же разметка при 200 — это обычная страница, не челлендж.
        self.assertFalse(ai.is_challenge(Resp(200, "https://chatgpt.com/", CF_CHALLENGE, HTML)))

    def test_openai_api(self) -> None:
        url = "https://api.openai.com/v1/models"
        self.assertEqual(ai.judge_openai_api(Resp(401, url, OPENAI_401, JSON)).verdict, ai.OK)
        v = ai.judge_openai_api(Resp(403, url, OPENAI_403, JSON))
        self.assertEqual((v.verdict, v.note), (ai.BLOCKED, "403 unsupported_country"))
        chal = Resp(403, url, CF_CHALLENGE, {**HTML, "cf-mitigated": "challenge"})
        self.assertEqual(ai.judge_openai_api(chal).verdict, ai.CHALLENGE)
        self.assertEqual(ai.judge_openai_api(Resp(500, url, "{}", JSON)).verdict, ai.ERROR)

    def test_claude_web(self) -> None:
        ok = Resp(200, "https://claude.ai/login", CLAUDE_LOGIN, HTML, ["https://claude.ai/"])
        v = ai.judge_claude_web(ok)
        self.assertEqual((v.verdict, v.note), (ai.OK, "200 /login"))
        region = Resp(200, "https://claude.com/app-unavailable-in-region", CLAUDE_REGION, HTML,
                      ["https://claude.ai/"])
        self.assertEqual(ai.judge_claude_web(region).verdict, ai.BLOCKED)
        chal = Resp(403, "https://claude.ai/login", CF_CHALLENGE, {**HTML, "cf-mitigated": "challenge"},
                    ["https://claude.ai/"])
        self.assertEqual(ai.judge_claude_web(chal).verdict, ai.CHALLENGE)
        # Редирект куда-то ещё — не «ok», даже с кодом 200.
        other = Resp(200, "https://www.anthropic.com/", "<html><title>Anthropic</title></html>", HTML)
        self.assertEqual(ai.judge_claude_web(other).verdict, ai.ERROR)

    def test_anthropic_api(self) -> None:
        url = "https://api.anthropic.com/v1/models"
        self.assertEqual(ai.judge_anthropic_api(Resp(401, url, ANTHROPIC_401, JSON)).verdict, ai.OK)
        v = ai.judge_anthropic_api(Resp(403, url, ANTHROPIC_403, JSON))
        self.assertEqual((v.verdict, v.note), (ai.BLOCKED, "403 Request not allowed"))
        chal = Resp(403, url, CF_CHALLENGE, {**HTML, "cf-mitigated": "challenge"})
        self.assertEqual(ai.judge_anthropic_api(chal).verdict, ai.CHALLENGE)

    def test_gemini_api(self) -> None:
        url = ai.GEMINI_API_URL
        self.assertEqual(ai.judge_gemini_api(Resp(200, url, '{"candidates":[]}', JSON)).verdict, ai.OK)
        loc = ('{"error":{"code":400,"message":"User location is not supported for the API use.",'
               '"status":"FAILED_PRECONDITION"}}')
        self.assertEqual(ai.judge_gemini_api(Resp(400, url, loc, JSON)).verdict, ai.BLOCKED)
        bad = '{"error":{"code":400,"status":"INVALID_ARGUMENT","details":[{"reason":"API_KEY_INVALID"}]}}'
        v = ai.judge_gemini_api(Resp(400, url, bad, JSON))
        self.assertEqual((v.verdict, v.note), (ai.ERROR, "400 ключ не принят"))


class GeminiTests(unittest.TestCase):
    def test_page_tokens(self) -> None:
        tok = ai.gemini_page(GEMINI_PAGE)
        self.assertIsNotNone(tok)
        assert tok is not None
        self.assertEqual(tok.bl, "boq_assistant-bard-web-server_20260101.00_p0")
        self.assertEqual(tok.sid, "-1234567890")
        self.assertEqual(tok.at, "AFakeToken:1")
        self.assertEqual(tok.cc, "RUS")
        self.assertIsNone(ai.judge_gemini_page(Resp(200, ai.URLS["gemini_web"], GEMINI_PAGE, HTML)))

    def test_page_without_tokens_or_captcha(self) -> None:
        empty = Resp(200, ai.URLS["gemini_web"], "<html><body>consent</body></html>", HTML)
        self.assertEqual(ai.judge_gemini_page(empty).verdict, ai.ERROR)
        sorry = Resp(429, "https://www.google.com/sorry/index?continue=x", "Our systems have detected "
                     "unusual traffic", HTML)
        self.assertEqual(ai.judge_gemini_page(sorry).verdict, ai.CHALLENGE)

    def test_answers(self) -> None:
        v = ai.judge_gemini_answer(GEMINI_OK, "BGR")
        self.assertEqual((v.verdict, v.note), (ai.OK, "ответ получен, Bulgaria"))
        v = ai.judge_gemini_answer(GEMINI_1060, "RUS")
        self.assertEqual((v.verdict, v.note), (ai.BLOCKED, "ошибка 1060, страна RUS"))
        v = ai.judge_gemini_answer(GEMINI_OTHER_ERR)
        self.assertEqual((v.verdict, v.note), (ai.ERROR, "ошибка 7/1037"))
        self.assertEqual(ai.judge_gemini_answer("").verdict, ai.ERROR)


class MiscTests(unittest.TestCase):
    def test_trace(self) -> None:
        tr = ai.parse_trace(TRACE)
        self.assertEqual((tr["ip"], tr["loc"], tr["warp"]), ("104.28.0.1", "PL", "on"))

    def test_exceptions(self) -> None:
        class Timeout(Exception):
            pass
        self.assertEqual(ai.classify_exception(Timeout("x")).verdict, ai.TIMEOUT)
        self.assertEqual(ai.classify_exception(
            RuntimeError("Failed to perform, curl: (28) Connection timed out after 20001 ms")).verdict, ai.TIMEOUT)
        v = ai.classify_exception(RuntimeError("Failed to perform, curl: (97) Can't complete SOCKS5 connection."))
        self.assertEqual(v.verdict, ai.ERROR)
        self.assertTrue(v.note.startswith("curl: (97)"))

    def test_host_key(self) -> None:
        self.assertEqual(ai.host_key("🇦🇱 🟩 📡 AL WCxH 🎮 🧠"), "AL WCxH")
        self.assertEqual(ai.host_key("🇦🇱 🟢 📡 AL WCxH 🎮"), "AL WCxH")   # сняли 🧠 — ключ тот же
        self.assertEqual(ai.host_key("🇫🇷 🟢 📡 FR ☢ 🎮 🧠 📱"), "FR ☢")     # ☢ — часть имени
        self.assertEqual(ai.host_key("🇦🇹 🔵 📰 LTE • AT • YA"), "LTE • AT • YA")
        self.assertEqual(ai.host_key("🇷🇺 🟢 📡 RU • MSK 📺 🎮 🧠 📱"), "RU • MSK")

    def test_is_due(self) -> None:
        now = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
        self.assertTrue(ai.is_due(None, 12, now))
        done = SimpleNamespace(status="done", started_at=now - timedelta(hours=11))
        self.assertFalse(ai.is_due(done, 12, now))
        done.started_at = now - timedelta(hours=12, minutes=1)
        self.assertTrue(ai.is_due(done, 12, now))
        # Упавший прогон повторяем через час, а не через полсуток.
        failed = SimpleNamespace(status="failed", started_at=now - timedelta(minutes=61))
        self.assertTrue(ai.is_due(failed, 12, now))
        failed.started_at = now - timedelta(minutes=30)
        self.assertFalse(ai.is_due(failed, 12, now))
        # Отменённый человеком — не упавший: через час сам не перезапускается.
        cancelled = SimpleNamespace(status="cancelled", started_at=now - timedelta(minutes=61))
        self.assertFalse(ai.is_due(cancelled, 12, now))
        cancelled.started_at = now - timedelta(hours=12, minutes=1)
        self.assertTrue(ai.is_due(cancelled, 12, now))


def _profile(remark: str, **extra) -> dict:
    proxy = {"tag": "proxy", "protocol": "vless",
             "settings": {"vnext": [{"address": "203.0.113.1", "port": 443, "users": [{"id": "u"}]}]},
             "streamSettings": {"network": "xhttp", "security": "reality",
                                "xhttpSettings": {"path": "/x", "extra": {"xPaddingBytes": "100-1000"}}}}
    p = {"remarks": remark, "outbounds": [proxy, {"tag": "direct", "protocol": "freedom"}],
         "routing": {"rules": []}, "inbounds": [{"tag": "socks", "port": 10808}]}
    p.update(extra)
    return p


class TargetTests(unittest.TestCase):
    def test_build(self) -> None:
        balancer = _profile("⚡ 🟢 АВТО • быстрый")
        balancer["outbounds"].insert(1, {"tag": "proxy-2", "protocol": "vless"})
        balancer["routing"] = {"balancers": [{"tag": "auto", "selector": ["proxy"]}]}
        chained = _profile("🇫🇷 🟢 📡 FR ☢")
        chained["outbounds"][0]["streamSettings"]["sockopt"] = {"dialerProxy": "hop"}
        chained["outbounds"].append({"tag": "hop", "protocol": "vless", "settings": {}})
        broken = {"remarks": "🇽🇽 BROKEN", "outbounds": [{"tag": "direct", "protocol": "freedom"}]}
        profiles = [balancer, _profile("🇩🇪 🟩 📡 DE 🎮 🧠"), chained, broken,
                    _profile("🇩🇪 🟢 📡 DE 🎮"), _profile("🇸🇪 SE • L4")]

        targets = ai.build_targets(profiles, exclude="se • l4")
        self.assertEqual([t.key for t in targets], ["DE", "FR ☢", "BROKEN", "DE (2)"])

        de = targets[0]
        assert de.outbounds is not None
        xs = de.outbounds[0]["streamSettings"]["xhttpSettings"]
        self.assertNotIn("extra", xs)                      # extra развёрнут
        self.assertEqual(xs["xPaddingBytes"], "100-1000")
        self.assertEqual([o["tag"] for o in de.outbounds], ["proxy", "direct"])
        # Профиль из подписки не испорчен: копия, а не правка на месте.
        self.assertIn("extra", profiles[1]["outbounds"][0]["streamSettings"]["xhttpSettings"])

        fr = targets[1]
        assert fr.outbounds is not None
        self.assertEqual([o["tag"] for o in fr.outbounds], ["proxy", "hop", "direct"])
        self.assertEqual(targets[2].error, "в профиле нет outbound proxy")

    def test_fetch_profiles_falls_back_to_json_type(self) -> None:
        calls: list[str] = []

        def fake(url: str, ua: str):
            calls.append(url)
            return None if not url.endswith("/json") else [_profile("DE")]

        with mock.patch.object(ai, "_get_json", side_effect=fake):
            got = ai.fetch_profiles("https://sub.example.com/sub/abc")
        self.assertEqual(len(got), 1)
        self.assertEqual(calls, ["https://sub.example.com/sub/abc", "https://sub.example.com/sub/abc/json"])

        with mock.patch.object(ai, "_get_json", return_value=None):
            with self.assertRaises(RuntimeError):
                ai.fetch_profiles("https://sub.example.com/sub/abc")
        with self.assertRaises(RuntimeError):
            ai.fetch_profiles("")


class DiffTests(unittest.TestCase):
    def _row(self, host, cc="PL", **verdicts):
        return {"host": host, "ai_country": cc,
                "results": {k: {"v": v, "note": ""} for k, v in verdicts.items()}}

    def test_diff_and_summary(self) -> None:
        prev = {"DE": self._row("DE", chatgpt_web="ok", claude_web="ok"),
                "FI": self._row("FI", claude_web="challenge"),
                "NO": self._row("NO <b>", chatgpt_web="ok"),
                "UA": self._row("UA", chatgpt_web="ok")}
        cur = {"DE": self._row("DE", cc="DE", chatgpt_web="ok", claude_web="blocked"),
               "FI": self._row("FI", claude_web="ok"),
               "NO": self._row("NO <b>", chatgpt_web="timeout"),
               "NEW": self._row("NEW", chatgpt_web="blocked")}
        changes = ai.diff_runs(prev, cur)
        got = {(c["host_key"], c["service"], c["before"], c["after"]) for c in changes}
        self.assertEqual(got, {("DE", "claude_web", "ok", "blocked"), ("DE", "ai_exit", "PL", "DE"),
                               ("FI", "claude_web", "challenge", "ok"), ("NO", "chatgpt_web", "ok", "timeout")})

        broke, fixed, moved = ai.notable(changes)
        self.assertEqual([c["host_key"] for c in broke], ["DE"])
        self.assertEqual([c["host_key"] for c in fixed], ["FI"])
        self.assertEqual([c["host_key"] for c in moved], ["DE"])

        text = ai.summary_text(changes)
        assert text is not None
        self.assertIn("Сломалось", text)
        self.assertIn("Claude (ok → blocked)", text)
        self.assertIn("Починилось", text)
        self.assertIn("PL → DE", text)
        self.assertNotIn("NO", text)          # таймаут — не повод будить людей

    def test_nothing_notable(self) -> None:
        changes = [{"host": "X", "host_key": "X", "service": "chatgpt_web", "before": "ok", "after": "timeout"}]
        self.assertIsNone(ai.summary_text(changes))
        self.assertIsNone(ai.summary_text([]))

    def test_summary_escapes_html(self) -> None:
        changes = [{"host": "A<b>&", "host_key": "A", "service": "chatgpt_web", "before": "ok", "after": "blocked"}]
        text = ai.summary_text(changes)
        assert text is not None
        self.assertIn("A&lt;b&gt;&amp;", text)


class ProbeTests(unittest.TestCase):
    """Один хост целиком: туннель и сеть подменены, проверяем ход и повторы."""

    def setUp(self) -> None:
        self.sleep = mock.patch.object(ai.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    @staticmethod
    @contextmanager
    def _tunnel(_outbounds, _cancel=None):
        yield "socks5h://127.0.0.1:1"

    def _target(self) -> ai.Target:
        return ai.Target(name="🇩🇪 DE 🧠", key="DE", outbounds=[{"tag": "proxy"}])

    def test_full_host(self) -> None:
        answers = {
            ai.URLS["trace_direct"]: Resp(200, "", "ip=198.51.100.2\nloc=DE\nwarp=off\n"),
            ai.URLS["trace_ai"]: Resp(200, "", TRACE),
            ai.URLS["chatgpt_web"]: Resp(200, "https://chatgpt.com/", CHATGPT_OK, HTML),
            ai.URLS["openai_api"]: Resp(401, "https://api.openai.com/v1/models", OPENAI_401, JSON),
            ai.URLS["anthropic_api"]: Resp(403, "https://api.anthropic.com/v1/models", ANTHROPIC_403, JSON),
        }
        claude_calls = iter([
            RuntimeError("Failed to perform, curl: (28) Operation timed out"),
            Resp(403, "https://claude.ai/login", CF_CHALLENGE, {**HTML, "cf-mitigated": "challenge"}),
            Resp(200, "https://claude.ai/login", CLAUDE_LOGIN, HTML),
        ])

        def fake_get(url, proxy, timeout=ai.REQ_TIMEOUT, **kw):
            if url == ai.URLS["claude_web"]:
                nxt = next(claude_calls)
                if isinstance(nxt, Exception):
                    raise nxt
                return nxt
            return answers[url]

        with mock.patch.object(ai, "xray_tunnel", self._tunnel), \
                mock.patch.object(ai, "_get", side_effect=fake_get), \
                mock.patch.object(ai, "_gemini_web", return_value=ai.Verdict(ai.BLOCKED, "ошибка 1060")):
            out = ai.probe_target(self._target())

        self.assertEqual(out["tunnel"], "up")
        self.assertEqual((out["exit_ip"], out["exit_country"]), ("198.51.100.2", "DE"))
        self.assertEqual((out["ai_ip"], out["ai_country"], out["ai_warp"]), ("104.28.0.1", "PL", "on"))
        res = out["results"]
        self.assertNotIn("gemini_api", res)            # без ключа не проверяем
        self.assertEqual(res["chatgpt_web"]["v"], ai.OK)
        self.assertEqual(res["openai_api"]["v"], ai.OK)
        self.assertEqual(res["anthropic_api"]["v"], ai.BLOCKED)
        self.assertEqual(res["gemini_web"]["v"], ai.BLOCKED)
        # Таймаут и челлендж перебиты определённым ответом на третьей попытке.
        self.assertEqual((res["claude_web"]["v"], res["claude_web"]["tries"]), (ai.OK, 3))

    def test_tunnel_down(self) -> None:
        with mock.patch.object(ai, "xray_tunnel", self._tunnel), \
                mock.patch.object(ai, "_get", side_effect=RuntimeError("curl: (28) timed out")):
            out = ai.probe_target(self._target(), gemini_key="k")
        self.assertEqual(out["tunnel"], "down")
        self.assertEqual({r["v"] for r in out["results"].values()}, {ai.TIMEOUT})
        self.assertIn("gemini_api", out["results"])

    def test_xray_failed(self) -> None:
        @contextmanager
        def broken(_o, _cancel=None):
            raise ai.TunnelError("xray не запустился: failed to load config")
            yield  # pragma: no cover

        with mock.patch.object(ai, "xray_tunnel", broken):
            out = ai.probe_target(self._target())
        self.assertEqual(out["tunnel"], "error")
        self.assertIn("failed to load config", out["error"])
        self.assertEqual({r["v"] for r in out["results"].values()}, {ai.ERROR})

    def test_target_without_config(self) -> None:
        out = ai.probe_target(ai.Target(name="X", key="X", error="в профиле нет outbound proxy"))
        self.assertEqual(out["error"], "в профиле нет outbound proxy")
        self.assertEqual({r["v"] for r in out["results"].values()}, {ai.ERROR})

    def test_budget_stops_retries(self) -> None:
        calls = []

        def slow():
            calls.append(1)
            return ai.Verdict(ai.TIMEOUT, "нет ответа")

        with mock.patch.object(ai.time, "monotonic", side_effect=[0.0, 10.0, 99999.0]):
            v, n = ai._attempts(slow, deadline=100.0)
        self.assertEqual((v.verdict, n), (ai.TIMEOUT, 2))


class TunnelTests(unittest.TestCase):
    def test_missing_binary(self) -> None:
        with mock.patch.object(ai, "XRAY_BIN", "/nonexistent/xray"), \
                mock.patch.object(ai.shutil, "which", return_value=None):
            with self.assertRaises(ai.TunnelError):
                with ai.xray_tunnel([{"tag": "proxy"}]):
                    pass


# Стоит на месте xray в тесте с настоящим процессом: слушает socks-порт из конфига.
FAKE_XRAY = """
import json, socket, sys
cfg = json.load(open(sys.argv[-1]))        # xray run -c <конфиг>
s = socket.socket()
s.bind(("127.0.0.1", cfg["inbounds"][0]["port"]))
s.listen(5)
while True:
    s.accept()[0].close()
"""


class CancelTests(unittest.TestCase):
    """Отмена проверки одного хоста: флаг, процесс xray, временные файлы."""

    def setUp(self) -> None:
        mock.patch.object(ai, "RETRY_PAUSE", 0.0).start()
        self.addCleanup(mock.patch.stopall)

    def test_token_kills_attached_process(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = None
        tok = ai.CancelToken()
        tok.attach(proc)
        proc.terminate.assert_not_called()
        tok.cancel()
        proc.terminate.assert_called_once()
        self.assertTrue(tok.cancelled)
        with self.assertRaises(ai.ProbeCancelled):
            tok.check()

        # Отменили раньше, чем процесс успел запуститься, — гасим при подключении.
        late = mock.Mock()
        late.poll.return_value = None
        tok.attach(late)
        late.terminate.assert_called_once()

        # После уборки туннеля токен процесс не трогает.
        tok2, gone = ai.CancelToken(), mock.Mock()
        tok2.attach(gone)
        tok2.detach()
        tok2.cancel()
        gone.terminate.assert_not_called()

    def test_cancel_interrupts_probe_and_leaves_no_result(self) -> None:
        tok = ai.CancelToken()
        closed: list[bool] = []

        @contextmanager
        def tunnel(_outbounds, _cancel=None):
            try:
                yield "socks5h://127.0.0.1:1"
            finally:
                closed.append(True)

        def fake_get(url, proxy, timeout=ai.REQ_TIMEOUT, **kw):
            if url == ai.URLS["trace_direct"]:
                return Resp(200, "", "ip=198.51.100.2\nloc=DE\n")
            if url == ai.URLS["trace_ai"]:
                return Resp(200, "", TRACE)
            # Отмена пришла посреди запроса: xray погашен, запрос падает.
            tok.cancel()
            raise RuntimeError("Failed to perform, curl: (97) Can't complete SOCKS5 connection.")

        target = ai.Target(name="🇩🇪 DE 🧠", key="DE", outbounds=[{"tag": "proxy"}])
        with mock.patch.object(ai, "xray_tunnel", tunnel), \
                mock.patch.object(ai, "_get", side_effect=fake_get):
            with self.assertRaises(ai.ProbeCancelled):
                ai.probe_target(target, cancel=tok)
        self.assertEqual(closed, [True])        # туннель убран обычным путём

    def test_tunnel_cleans_up_on_cancel(self) -> None:
        tok = ai.CancelToken()
        made: list[str] = []
        real_mkdtemp = ai.tempfile.mkdtemp

        def mkdtemp(**kw):
            made.append(real_mkdtemp(**kw))
            return made[-1]

        class FakeProc:
            returncode = None
            terminated = 0

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated += 1
                self.returncode = -15

            def wait(self, timeout=None):
                return self.returncode

        proc = FakeProc()

        def no_port(*_a, **_kw):
            tok.cancel()                 # отмена, пока xray ещё поднимается
            raise OSError("refused")

        with mock.patch.object(ai.shutil, "which", return_value="xray"), \
                mock.patch.object(ai.tempfile, "mkdtemp", side_effect=mkdtemp), \
                mock.patch.object(ai.subprocess, "Popen", return_value=proc), \
                mock.patch.object(ai.socket, "create_connection", side_effect=no_port), \
                mock.patch.object(ai.time, "sleep"):
            # Именно отмена, а не «xray не запустился»: процесс погасили мы сами.
            with self.assertRaises(ai.ProbeCancelled):
                with ai.xray_tunnel([{"tag": "proxy"}], tok):
                    self.fail("туннель не должен подняться")
        self.assertEqual(proc.terminated, 1)
        self.assertEqual(len(made), 1)
        self.assertFalse(os.path.exists(made[0]))      # временный каталог удалён

    def test_real_process_is_stopped_and_port_freed(self) -> None:
        """То же с настоящим процессом: вместо xray — скрипт, слушающий socks-порт."""
        real_popen, real_mkdtemp = ai.subprocess.Popen, ai.tempfile.mkdtemp
        procs: list = []
        made: list[str] = []

        def popen(cmd, **kw):
            procs.append(real_popen([sys.executable, "-c", FAKE_XRAY, *cmd[1:]], **kw))
            return procs[-1]

        def mkdtemp(**kw):
            made.append(real_mkdtemp(**kw))
            return made[-1]

        tok = ai.CancelToken()
        seen: dict = {}
        up = threading.Event()

        def work() -> None:
            try:
                with ai.xray_tunnel([{"tag": "proxy", "protocol": "freedom"}], tok) as proxy:
                    seen["port"] = int(proxy.rsplit(":", 1)[1])
                    up.set()
                    ai._pause(30, tok)          # «проверка идёт», пока не отменят
            except ai.ProbeCancelled:
                seen["cancelled"] = True
            except ai.TunnelError as exc:
                seen["error"] = str(exc)
            finally:
                up.set()

        with mock.patch.object(ai.shutil, "which", return_value="xray"), \
                mock.patch.object(ai.subprocess, "Popen", side_effect=popen), \
                mock.patch.object(ai.tempfile, "mkdtemp", side_effect=mkdtemp):
            th = threading.Thread(target=work)
            th.start()
            self.assertTrue(up.wait(20))
            self.assertIn("port", seen, seen.get("error"))
            started = time.monotonic()
            tok.cancel()
            th.join(20)
        self.assertFalse(th.is_alive())
        self.assertLess(time.monotonic() - started, 10)     # отмена не ждёт таймаутов
        self.assertTrue(seen.get("cancelled"))
        self.assertIsNotNone(procs[0].poll())               # процесс завершён
        self.assertFalse(os.path.exists(made[0]))           # каталог с конфигом удалён
        with self.assertRaises(OSError):                    # порт свободен
            socket.create_connection(("127.0.0.1", seen["port"]), timeout=2).close()

    def test_cancelled_before_start_spawns_nothing(self) -> None:
        tok = ai.CancelToken()
        tok.cancel()
        with mock.patch.object(ai.shutil, "which", return_value="xray"), \
                mock.patch.object(ai.subprocess, "Popen") as popen:
            with self.assertRaises(ai.ProbeCancelled):
                with ai.xray_tunnel([{"tag": "proxy"}], tok):
                    pass
        popen.assert_not_called()


def _result(t: ai.Target, verdict: str = ai.OK) -> dict:
    return {"host": t.name, "host_key": t.key, "tunnel": "up", "exit_ip": "198.51.100.2",
            "exit_country": "DE", "ai_ip": "104.28.0.1", "ai_country": "PL", "ai_warp": "on",
            "results": {"chatgpt_web": {"v": verdict, "note": "200", "tries": 1}},
            "error": None, "duration_ms": 5}


class RunTests(unittest.IsolatedAsyncioTestCase):
    """Прогоны целиком: очередь, выборка, отмена. Подписка, xray и база подменены."""

    HOSTS = ["🇩🇪 🟢 DE 🧠", "🇫🇷 🟢 FR 🧠", "🇸🇪 🟢 SE", "🇵🇱 🟢 PL 🧠"]

    async def asyncSetUp(self) -> None:
        ai._current = ai._next = ai._driver = None
        self.cfg = {**ai.DEFAULTS, "concurrency": 1}
        self.runs: dict[uuid.UUID, dict] = {}       # «таблица» ai_runs
        self.checks: list[tuple[uuid.UUID, str]] = []   # «таблица» ai_checks: (прогон, хост)
        self.started: list[str] = []                # порядок, в котором хосты пошли в проверку
        self.gates: dict[str, threading.Event] = {}  # хост висит в проверке, пока не отпустим
        self.fetch_gate: threading.Event | None = None
        self.profiles = [_profile(h) for h in self.HOSTS]
        self.summaries: list[str] = []
        self.changes: list[dict] = []

        async def create(run) -> None:
            self.runs[run.id] = {"scope": run.scope, "trigger": run.trigger, "status": "running",
                                 "error": None, "changes": None, "targets": run.snapshot()}
            run.created = True

        async def sync(run) -> None:
            if run.id in self.runs:
                self.runs[run.id]["targets"] = run.snapshot()

        async def finish(run, status, error=None, changes=None) -> None:
            self.runs[run.id].update(status=status, error=error, changes=changes,
                                     targets=run.snapshot())

        async def save(run, item, res) -> None:
            self.checks.append((run.id, res["host_key"]))

        async def context():
            return self.cfg, "", "https://sub.example.com/sub/x"

        async def changes(_run):
            return self.changes

        async def summary(_cfg, text):
            self.summaries.append(text)
            return None

        def fetch(_url):
            if self.fetch_gate is not None:
                self.fetch_gate.wait(5)
            return self.profiles

        def probe(t, _gemini="", cancel=None):
            self.started.append(t.key)
            gate = self.gates.get(t.key)
            deadline = time.monotonic() + 5
            while gate is not None and not gate.is_set() and time.monotonic() < deadline:
                cancel.check()
                time.sleep(0.002)
            cancel.check()
            return _result(t)

        for name, fake in (("_db_create", create), ("_db_sync", sync), ("_db_finish", finish),
                           ("_db_save_check", save), ("_db_context", context),
                           ("_db_changes", changes), ("_send_summary", summary),
                           ("fetch_profiles", fetch), ("probe_target", probe)):
            mock.patch.object(ai, name, fake).start()
        self.addCleanup(mock.patch.stopall)

    async def asyncTearDown(self) -> None:
        for g in self.gates.values():
            g.set()
        if self.fetch_gate is not None:
            self.fetch_gate.set()
        await ai.shutdown()
        ai._current = ai._next = ai._driver = None

    def _hold(self, *keys: str) -> None:
        for k in keys:
            self.gates[k] = threading.Event()

    async def _until(self, cond, what: str = "условие") -> None:
        deadline = time.monotonic() + 5
        while not cond():
            if time.monotonic() > deadline:
                self.fail(f"не дождались: {what}")
            await asyncio.sleep(0.002)

    async def _idle(self) -> None:
        await self._until(lambda: not ai.is_running(), "конец прогона")

    def _states(self) -> dict[str, str]:
        live = ai.live()
        return {h["key"]: h["state"] for h in live["hosts"]} if live else {}

    def _checked(self) -> list[str]:
        return [key for _, key in self.checks]

    def _only_run(self) -> dict:
        self.assertEqual(len(self.runs), 1)
        return next(iter(self.runs.values()))

    async def test_full_run_checks_everything_and_reports(self) -> None:
        self.changes = [{"host": "DE", "host_key": "DE", "service": "chatgpt_web",
                         "before": "ok", "after": "blocked"}]
        await ai.start_run("schedule")
        await self._idle()
        run = self._only_run()
        self.assertEqual((run["scope"], run["trigger"], run["status"]), ("full", "schedule", "done"))
        self.assertEqual(self._checked(), ["DE", "FR", "SE", "PL"])    # порядок подписки
        self.assertEqual(run["changes"], self.changes)
        self.assertEqual(len(self.summaries), 1)                         # сводка ушла
        self.assertEqual({t["state"] for t in run["targets"]}, {ai.DONE})
        self.assertIsNone(ai.live())

    async def test_selected_hosts_only(self) -> None:
        self.changes = [{"host": "FR", "host_key": "FR", "service": "chatgpt_web",
                         "before": "ok", "after": "blocked"}]
        await ai.start_run("manual", ["FR", "PL"])
        await self._idle()
        run = self._only_run()
        self.assertEqual((run["scope"], run["status"]), ("partial", "done"))
        self.assertEqual(self._checked(), ["FR", "PL"])
        # Выборочный прогон — не событие для Телеграма и не основа для сравнения.
        self.assertEqual(self.summaries, [])
        self.assertIsNone(run["changes"])
        # Имя и место в подписке подтянулись из свежей подписки.
        self.assertEqual([(t["key"], t["host"], t["pos"]) for t in run["targets"]],
                         [("FR", self.HOSTS[1], 1), ("PL", self.HOSTS[3], 3)])

    async def test_status_shows_each_host_state(self) -> None:
        self._hold("DE")
        await ai.start_run("manual", ["DE", "FR"])
        await self._until(lambda: self._states().get("DE") == ai.RUNNING, "DE проверяется")
        live = ai.live()
        self.assertEqual((live["scope"], live["resolved"], live["stopping"]), ("partial", True, False))
        self.assertEqual(self._states(), {"DE": ai.RUNNING, "FR": ai.QUEUED})
        # То же состояние ложится в базу — его увидит и список прогонов.
        await self._until(lambda: {t["key"]: t["state"] for t in self._only_run()["targets"]}
                          == {"DE": ai.RUNNING, "FR": ai.QUEUED}, "состояние в базе")

        self.gates["DE"].set()
        self._hold("FR")
        await self._until(lambda: self._states().get("FR") == ai.RUNNING, "FR проверяется")
        self.assertEqual(self._states()["DE"], ai.DONE)      # готовый виден, пока прогон идёт
        self.gates["FR"].set()
        await self._idle()
        self.assertIsNone(ai.live())                          # прогона нет — спиннеров нет

    async def test_host_joins_running_run_without_duplicates(self) -> None:
        self._hold("DE")
        await ai.start_run("manual", ["DE"])
        await self._until(lambda: self._states().get("DE") == ai.RUNNING, "DE проверяется")

        await ai.start_run("manual", ["FR", "DE"])     # DE уже идёт — второй раз не встанет
        await ai.start_run("manual", ["FR"])           # FR уже ждёт
        self.assertEqual(self._states(), {"DE": ai.RUNNING, "FR": ai.QUEUED})
        self.assertEqual(len(self.runs), 1)            # тот же прогон, а не новый и не 409

        self.gates["DE"].set()
        await self._idle()
        self.assertEqual(self._checked(), ["DE", "FR"])
        self.assertEqual(self._only_run()["status"], "done")

    async def test_concurrency_limit_and_free_slot_for_late_host(self) -> None:
        self.cfg["concurrency"] = 2
        self._hold("DE", "FR", "SE")
        await ai.start_run("manual", ["DE", "FR", "SE"])
        await self._until(lambda: self._states().get("FR") == ai.RUNNING, "DE и FR проверяются")
        self.assertEqual(self._states(), {"DE": ai.RUNNING, "FR": ai.RUNNING, "SE": ai.QUEUED})
        self.gates["FR"].set()
        self.gates["SE"].set()
        await self._until(lambda: self._states().get("SE") == ai.DONE, "SE проверен")

        # Место свободно — хост, попрошенный посреди прогона, идёт в проверку сразу,
        # а не после того, как закончится DE.
        self._hold("PL")
        await ai.start_run("manual", ["PL"])
        await self._until(lambda: self._states().get("PL") == ai.RUNNING, "PL проверяется")
        self.assertEqual(self._states()["DE"], ai.RUNNING)
        self.gates["DE"].set()
        self.gates["PL"].set()
        await self._idle()
        self.assertEqual(sorted(self._checked()), ["DE", "FR", "PL", "SE"])
        self.assertEqual(len(self.runs), 1)

    async def test_finished_host_can_be_rechecked_in_same_run(self) -> None:
        self._hold("FR")
        await ai.start_run("manual", ["DE", "FR"])
        await self._until(lambda: self._states().get("FR") == ai.RUNNING, "FR проверяется")
        await ai.start_run("manual", ["DE"])           # DE уже готов — проверим ещё раз
        self.assertEqual(self._states()["DE"], ai.QUEUED)
        self.gates["FR"].set()
        await self._idle()
        self.assertEqual(self._checked(), ["DE", "FR", "DE"])
        self.assertEqual(len(self.runs), 1)

    async def test_cancel_one_host(self) -> None:
        self._hold("DE")
        await ai.start_run("manual", ["DE", "FR", "SE"])
        await self._until(lambda: self._states().get("DE") == ai.RUNNING, "DE проверяется")

        await ai.cancel_run(["FR"])                    # из очереди — сразу
        self.assertEqual(self._states()["FR"], ai.CANCELLED)
        await ai.cancel_run(["DE"])                    # идущая проверка — прерывается
        self.assertIn(self._states()["DE"], (ai.CANCELLING, ai.CANCELLED))
        await self._idle()

        self.assertEqual(self._checked(), ["SE"])      # остальные проверены
        self.assertEqual(self.started, ["DE", "SE"])   # FR до проверки не дошёл
        run = self._only_run()
        # Отменённые — не ошибка: строки результата для них просто нет.
        self.assertEqual({t["key"]: t["state"] for t in run["targets"]},
                         {"DE": ai.CANCELLED, "FR": ai.CANCELLED, "SE": ai.DONE})
        self.assertEqual(run["status"], "done")

    async def test_cancel_whole_run_keeps_finished_hosts(self) -> None:
        self._hold("FR")
        await ai.start_run("schedule")
        await self._until(lambda: self._states().get("FR") == ai.RUNNING, "FR проверяется")
        await ai.cancel_run()
        self.assertTrue(ai.live()["stopping"])
        await self._idle()

        run = self._only_run()
        self.assertEqual(run["status"], "cancelled")
        self.assertIsNone(run["error"])
        self.assertEqual(self._checked(), ["DE"])      # проверенное до отмены сохранено
        self.assertEqual({t["key"]: t["state"] for t in run["targets"]},
                         {"DE": ai.DONE, "FR": ai.CANCELLED, "SE": ai.CANCELLED, "PL": ai.CANCELLED})
        self.assertEqual(self.summaries, [])           # оборванный прогон не сравниваем

    async def test_cancel_host_before_subscription_arrives(self) -> None:
        self.fetch_gate = threading.Event()
        await ai.start_run("manual")
        live = ai.live()
        self.assertEqual((live["scope"], live["resolved"], live["hosts"]), ("full", False, []))
        await ai.cancel_run(["SE"])                    # хосты ещё неизвестны — запомнили
        self.assertEqual(ai.live()["skipped"], {"SE"})
        self.fetch_gate.set()
        await self._idle()

        run = self._only_run()
        self.assertEqual(self._checked(), ["DE", "FR", "PL"])
        self.assertEqual(run["status"], "done")        # полный прогон состоялся, без одного хоста
        self.assertEqual({t["key"]: t["state"] for t in run["targets"]}["SE"], ai.CANCELLED)

    async def test_host_missing_from_subscription(self) -> None:
        await ai.start_run("manual", ["DE", "GONE"])
        await self._idle()
        run = self._only_run()
        self.assertEqual(self._checked(), ["DE"])      # остальные проверяются
        self.assertEqual(run["status"], "done")
        gone = next(t for t in run["targets"] if t["key"] == "GONE")
        self.assertEqual((gone["state"], gone["error"]), (ai.MISSING, ai.MISSING_NOTE))

    async def test_only_missing_hosts_is_a_failure(self) -> None:
        await ai.start_run("manual", ["GONE"])
        await self._idle()
        run = self._only_run()
        self.assertEqual(run["status"], "failed")
        self.assertIn("GONE", run["error"])
        self.assertEqual(self.checks, [])

    async def test_excluded_host_is_reported_not_checked(self) -> None:
        self.cfg["exclude"] = "se"
        await ai.start_run("manual", ["SE", "PL"])
        await self._idle()
        self.assertEqual(self._checked(), ["PL"])
        se = next(t for t in self._only_run()["targets"] if t["key"] == "SE")
        self.assertEqual(se["state"], ai.MISSING)

    async def test_full_run_over_full_run_is_refused(self) -> None:
        self._hold("DE")
        await ai.start_run("manual")
        await self._until(lambda: self._states().get("DE") == ai.RUNNING, "DE проверяется")
        with self.assertRaises(ai.AlreadyRunning):
            await ai.start_run("manual")
        with self.assertRaises(ai.AlreadyRunning):
            await ai.start_run("schedule")
        # Хост, который в полном прогоне ещё ждёт, второй раз в очередь не встаёт.
        await ai.start_run("manual", ["PL"])
        self.assertEqual(list(self._states()), ["DE", "FR", "SE", "PL"])
        self.gates["DE"].set()
        await self._idle()
        self.assertEqual(self._checked(), ["DE", "FR", "SE", "PL"])
        self.assertEqual(len(self.runs), 1)

    async def test_scheduled_run_waits_for_selective_one(self) -> None:
        self._hold("DE")
        await ai.start_run("manual", ["DE"])
        await self._until(lambda: self._states().get("DE") == ai.RUNNING, "DE проверяется")

        await ai.start_run("schedule")                 # не 409 и не потерялся
        self.assertEqual(ai.live()["pending_full"], "schedule")
        self.assertEqual(len(self.runs), 1)            # строки в базе пока нет
        with self.assertRaises(ai.AlreadyRunning):     # второй плановый не копится
            await ai.start_run("schedule")
        # Выборочный тем временем продолжает принимать хосты.
        await ai.start_run("manual", ["FR"])
        self.assertEqual(self._states(), {"DE": ai.RUNNING, "FR": ai.QUEUED})

        self.gates["DE"].set()
        await self._idle()
        first, second = list(self.runs.values())
        self.assertEqual((first["scope"], first["status"]), ("partial", "done"))
        self.assertEqual((second["scope"], second["trigger"], second["status"]),
                         ("full", "schedule", "done"))
        self.assertEqual(self._checked(), ["DE", "FR", "DE", "FR", "SE", "PL"])

    async def test_cancel_all_drops_waiting_full_run(self) -> None:
        self._hold("DE")
        await ai.start_run("manual", ["DE"])
        await self._until(lambda: self._states().get("DE") == ai.RUNNING, "DE проверяется")
        await ai.start_run("manual")
        await ai.cancel_run()
        await self._idle()
        self.assertEqual(self._only_run()["status"], "cancelled")
        self.assertEqual(self.checks, [])

    async def test_hosts_asked_while_run_is_stopping_go_next(self) -> None:
        self._hold("DE")
        await ai.start_run("manual", ["DE", "FR"])
        await self._until(lambda: self._states().get("DE") == ai.RUNNING, "DE проверяется")
        await ai.cancel_run()
        await ai.start_run("manual", ["SE"])           # остановленный прогон хостов не берёт
        self.assertEqual(self._states().get("SE"), ai.QUEUED)
        await self._idle()
        first, second = list(self.runs.values())
        self.assertEqual(first["status"], "cancelled")
        self.assertEqual((second["scope"], second["status"]), ("partial", "done"))
        self.assertEqual(self._checked(), ["SE"])

    async def test_failed_subscription_fails_the_run(self) -> None:
        def broken(_url):
            raise RuntimeError("подписка ответила 502")

        with mock.patch.object(ai, "fetch_profiles", broken):
            await ai.start_run("manual", ["DE"])
            await self._idle()
        run = self._only_run()
        self.assertEqual((run["status"], run["error"]), ("failed", "подписка ответила 502"))
        self.assertIsNone(ai.live())

    async def test_shutdown_aborts_run_and_leaves_no_spinners(self) -> None:
        self._hold("DE")
        await ai.start_run("manual", ["DE", "FR"])
        await self._until(lambda: self._states().get("DE") == ai.RUNNING, "DE проверяется")
        await ai.shutdown()
        self.assertIsNone(ai.live())
        self.assertFalse(ai.is_running())
        run = self._only_run()
        self.assertEqual(run["status"], "aborted")
        self.assertEqual(self.checks, [])
        # Это обрыв, а не отмена человеком: хосты не помечаются отменёнными.
        self.assertEqual({t["key"]: t["state"] for t in run["targets"]},
                         {"DE": ai.CANCELLING, "FR": ai.QUEUED})

    async def test_schedule_counts_only_full_runs(self) -> None:
        now = ai.now()
        recent_full = SimpleNamespace(status="done", started_at=now - timedelta(hours=1))
        old_full = SimpleNamespace(status="done", started_at=now - timedelta(hours=13))

        # Расписание смотрит на последний ПОЛНЫЙ прогон: выборочные, сколько бы их
        # ни было после него, в last_full не попадают и срок не сдвигают.
        with mock.patch.object(ai, "last_full", mock.AsyncMock(return_value=recent_full)):
            await ai.schedule_tick(None, {"enabled": True, "interval_hours": 12})
        self.assertFalse(ai.is_running())

        with mock.patch.object(ai, "last_full", mock.AsyncMock(return_value=old_full)):
            await ai.schedule_tick(None, {"enabled": False, "interval_hours": 12})
            self.assertFalse(ai.is_running())          # выключено — не запускаем

            # Пора, а идёт выборочный прогон: плановый встаёт следом за ним.
            self._hold("DE")
            await ai.start_run("manual", ["DE"])
            await ai.schedule_tick(None, {"enabled": True, "interval_hours": 12})
            self.assertEqual(ai.live()["pending_full"], "schedule")
            with self.assertRaises(ai.AlreadyRunning):
                await ai.schedule_tick(None, {"enabled": True, "interval_hours": 12})
        self.gates["DE"].set()
        await self._idle()
        self.assertEqual([(r["scope"], r["trigger"]) for r in self.runs.values()],
                         [("partial", "manual"), ("full", "schedule")])


T0 = datetime(2026, 9, 30, 0, tzinfo=timezone.utc)


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


class MatrixTests(unittest.TestCase):
    """Матрица собирается из последнего результата каждого хоста."""

    def _run(self, started: float, scope: str = "full", status: str = "done"):
        return SimpleNamespace(id=uuid.uuid4(), scope=scope, status=status, started_at=_at(started))

    def _check(self, run, key: str, at: float, verdict: str = "ok", pos: int = 0):
        return (SimpleNamespace(host_key=key, host=key, checked_at=_at(at), position=pos,
                                results={"chatgpt_web": {"v": verdict}}, run_id=run.id), run)

    def _verdicts(self, rows) -> dict[str, tuple]:
        return {r.key: (r.check.results["chatgpt_web"]["v"],
                        r.before.results["chatgpt_web"]["v"] if r.before else None) for r in rows}

    def test_selective_run_updates_only_its_rows(self) -> None:
        f1, f2 = self._run(0), self._run(720)
        p = self._run(800, scope="partial")
        pairs = [
            self._check(f1, "DE", 1), self._check(f1, "FR", 2, "ok"), self._check(f1, "SE", 3),
            self._check(f2, "DE", 721), self._check(f2, "FR", 722, "blocked"), self._check(f2, "SE", 723),
            self._check(p, "FR", 801, "ok"),
        ]
        rows = ai.build_matrix(pairs, f2, f1, ["DE", "FR", "SE"])
        self.assertEqual([r.key for r in rows], ["DE", "FR", "SE"])     # все хосты на месте
        fr = rows[1]
        self.assertEqual(fr.check.checked_at, _at(801))                 # свежий результат и время
        self.assertTrue(fr.partial)
        # Выборочный сравнивается со своим же предыдущим результатом, остальные —
        # с предыдущим полным прогоном, как и раньше.
        self.assertEqual(self._verdicts(rows), {"DE": ("ok", "ok"), "FR": ("ok", "blocked"),
                                                "SE": ("ok", "ok")})
        self.assertEqual([r.partial for r in rows], [False, True, False])
        self.assertEqual([r.is_new for r in rows], [False, False, False])

    def test_full_run_compares_with_previous_full_not_with_selective(self) -> None:
        f1, p, f2 = self._run(0), self._run(100, scope="partial"), self._run(720)
        pairs = [self._check(f1, "DE", 1, "blocked"), self._check(p, "DE", 101, "ok"),
                 self._check(f2, "DE", 721, "ok")]
        rows = ai.build_matrix(pairs, f2, f1, ["DE"])
        self.assertEqual(self._verdicts(rows), {"DE": ("ok", "blocked")})
        self.assertFalse(rows[0].partial)

    def test_cancelled_host_keeps_previous_result(self) -> None:
        f1, f2 = self._run(0), self._run(720)
        # В f2 проверку SE отменили: строки за f2 у него нет, но в составе он есть.
        pairs = [self._check(f1, "DE", 1), self._check(f1, "SE", 2, "blocked"),
                 self._check(f2, "DE", 721)]
        rows = ai.build_matrix(pairs, f2, f1, ["DE", "SE"])
        self.assertEqual([r.key for r in rows], ["DE", "SE"])
        se = rows[1]
        self.assertEqual((se.check.checked_at, se.check.results["chatgpt_web"]["v"]), (_at(2), "blocked"))
        self.assertIsNone(se.before)
        self.assertFalse(se.is_new)

    def test_running_and_cancelled_full_runs_refresh_rows(self) -> None:
        f1, f2 = self._run(0), self._run(720)
        f3 = self._run(1440, status="running")
        pairs = [self._check(f1, "DE", 1), self._check(f1, "FR", 2),
                 self._check(f2, "DE", 721), self._check(f2, "FR", 722),
                 self._check(f3, "DE", 1441, "blocked")]
        rows = ai.build_matrix(pairs, f2, f1, ["DE", "FR"])
        # Готовая строка идущего прогона уже в матрице и сравнена с опорным прогоном.
        self.assertEqual(self._verdicts(rows), {"DE": ("blocked", "ok"), "FR": ("ok", "ok")})

    def test_hosts_outside_base(self) -> None:
        f1, f2 = self._run(0), self._run(720)
        p = self._run(800, scope="partial")
        pairs = [self._check(f1, "OLD", 1), self._check(f1, "DE", 2), self._check(f2, "DE", 721),
                 self._check(p, "NEW", 801, pos=7)]
        rows = ai.build_matrix(pairs, f2, f1, ["DE"])
        # Убранный из подписки хост не возвращается, проверенный позже — добавляется.
        self.assertEqual([r.key for r in rows], ["DE", "NEW"])
        # Новый хост в полном прогоне помечается как новый.
        rows = ai.build_matrix([*pairs, self._check(f2, "FI", 722)], f2, f1, ["DE", "FI"])
        self.assertEqual({r.key: r.is_new for r in rows}, {"DE": False, "FI": True, "NEW": False})

    def test_without_a_finished_full_run(self) -> None:
        p = self._run(10, scope="partial")
        f = self._run(20, status="cancelled")
        pairs = [self._check(p, "FR", 11, pos=1), self._check(f, "DE", 21, pos=0),
                 self._check(f, "FR", 22, "blocked", pos=1)]
        rows = ai.build_matrix(pairs, None, None, [])
        self.assertEqual([r.key for r in rows], ["DE", "FR"])           # по месту в подписке
        self.assertEqual(self._verdicts(rows), {"DE": ("ok", None), "FR": ("blocked", None)})

    def test_recheck_inside_one_run_takes_the_latest(self) -> None:
        f1 = self._run(0)
        pairs = [self._check(f1, "DE", 1, "timeout"), self._check(f1, "DE", 5, "ok")]
        rows = ai.build_matrix(pairs, f1, None, ["DE"])
        self.assertEqual(self._verdicts(rows), {"DE": ("ok", None)})


if __name__ == "__main__":
    unittest.main()
