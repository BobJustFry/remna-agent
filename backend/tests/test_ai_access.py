"""Тесты вердиктов проверки доступа к ИИ.

Тела ответов синтетические, но повторяют признаки настоящих из замера
29–30.09: страницу «Unable to load site», редирект Claude на
app-unavailable-in-region, челлендж Cloudflare, JSON-ошибки API, ответ
StreamGenerate с BardErrorInfo [1060]. Сеть и xray замоканы.
"""
import unittest
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
    def _tunnel(_outbounds):
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
        def broken(_o):
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


if __name__ == "__main__":
    unittest.main()
