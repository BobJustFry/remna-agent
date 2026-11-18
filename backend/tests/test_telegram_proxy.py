"""Прокси для Telegram: выбор способа, маскирование, хранение, detect-chat."""
import asyncio
import unittest
from unittest import mock

import httpx
from fastapi import HTTPException

from app.api import subwatch as api
from app.schemas import SubwatchConfigUpdate, TelegramProbeIn
from app.services import subwatch, telegram
from app.services.crypto import decrypt_secret

PROXY = "socks5://user1:S3cretPw@10.0.0.5:1080"
TOKEN = "123456789:AAEhBP0av28ZZZsecrettokenZZ"


class FakeDb:
    def __init__(self):
        self.rows = {}

    async def get(self, model, key):
        return self.rows.get(key)

    def add(self, row):
        self.rows[row.key] = row

    async def flush(self):
        pass

    async def commit(self):
        pass


def run(coro):
    return asyncio.run(coro)


def ok_handler(request):
    return httpx.Response(200, json={"ok": True, "result": {}})


class TransportTests(unittest.TestCase):
    def tearDown(self):
        telegram.set_proxy("")

    def _client(self, handler):
        real = httpx.Client
        seen = {}

        def factory(**kw):
            seen.update(kw)
            return real(transport=httpx.MockTransport(handler))
        return factory, seen

    def test_no_proxy_goes_direct(self):
        factory, seen = self._client(ok_handler)
        with mock.patch.object(telegram.httpx, "Client", factory):
            self.assertIsNone(telegram.send_message(TOKEN, "1", "hi"))
        self.assertIsNone(seen["proxy"])

    def test_proxy_is_passed_to_client(self):
        telegram.set_proxy(PROXY)
        factory, seen = self._client(ok_handler)
        with mock.patch.object(telegram.httpx, "Client", factory):
            telegram.send_message(TOKEN, "1", "hi")
            telegram.describe_chat(TOKEN, "1")
            telegram.get_me(TOKEN)
        self.assertEqual(seen["proxy"], PROXY)

    def test_real_proxy_clients_are_constructible(self):
        for url in ("http://u:p@1.2.3.4:3128", "socks5://u:p@1.2.3.4:1080",
                    "socks5h://u:p@1.2.3.4:1080"):
            httpx.Client(proxy=url).close()

    def test_errors_do_not_leak_secrets(self):
        telegram.set_proxy(PROXY)

        def boom(request):
            raise httpx.ConnectError(f"failed {PROXY} {TOKEN} S3cretPw")
        factory, _ = self._client(boom)
        with mock.patch.object(telegram.httpx, "Client", factory):
            err = telegram.send_message(TOKEN, "1", "hi")
        self.assertIn("недоступен через прокси", err)
        for secret in ("S3cretPw", "user1", TOKEN, "AAEhBP0av28"):
            self.assertNotIn(secret, err)

    def test_http_error_description_scrubbed(self):
        factory, _ = self._client(lambda r: httpx.Response(
            401, json={"ok": False, "description": f"Unauthorized {TOKEN}"}))
        with mock.patch.object(telegram.httpx, "Client", factory):
            err = telegram.send_message(TOKEN, "1", "hi")
        self.assertTrue(err.startswith("Telegram 401"))
        self.assertNotIn("AAEhBP0av28", err)

    def test_validate_and_mask(self):
        self.assertIsNone(telegram.validate_proxy(PROXY))
        self.assertIsNone(telegram.validate_proxy("http://h.example:3128"))
        self.assertIsNotNone(telegram.validate_proxy("ftp://h:1"))
        self.assertIsNotNone(telegram.validate_proxy("socks5://h"))
        masked = telegram.mask_proxy(PROXY)
        self.assertEqual(masked, "socks5://10.0.0.5:1080")
        self.assertNotIn("S3cretPw", masked)


class DetectTests(unittest.TestCase):
    def tearDown(self):
        telegram.set_proxy("")

    def test_detect_returns_chats_without_texts(self):
        updates = {"ok": True, "result": [
            {"update_id": 1, "message": {"text": "SECRET-MESSAGE-TEXT", "from": {"id": 5},
                                          "chat": {"id": 42, "type": "private",
                                                   "first_name": "Ivan", "username": "ivan"}}},
            {"update_id": 2, "message": {"text": "another private text",
                                          "chat": {"id": -100123, "type": "supergroup",
                                                   "title": "Ops"}}},
            {"update_id": 3, "message": {"text": "dup", "chat": {"id": 42, "type": "private"}}},
        ]}
        with mock.patch.object(telegram, "_request", return_value=updates):
            chats = telegram.detect_chats(TOKEN)
        self.assertEqual({c["id"] for c in chats}, {42, -100123})
        flat = repr(chats)
        self.assertNotIn("SECRET-MESSAGE-TEXT", flat)
        self.assertNotIn("another private text", flat)

    def _call(self, fn):
        with mock.patch.object(subwatch, "bot_token", mock.AsyncMock(return_value=TOKEN)), \
                mock.patch.object(subwatch, "apply_telegram_proxy", mock.AsyncMock()):
            return run(fn("u", FakeDb()))

    def test_endpoint_hint_when_empty(self):
        with mock.patch.object(telegram, "detect_chats", return_value=[]):
            out = self._call(api.detect_chat)
        self.assertEqual(out["chats"], [])
        self.assertIn("Напишите боту", out["hint"])

    def test_endpoint_unavailable_message_has_no_secrets(self):
        telegram.set_proxy(PROXY)
        err = telegram.TelegramError(telegram.scrub(f"таймаут {PROXY} {TOKEN}", TOKEN))
        with mock.patch.object(telegram, "detect_chats", side_effect=err):
            with self.assertRaises(HTTPException) as cm:
                self._call(api.detect_chat)
        self.assertEqual(cm.exception.status_code, 502)
        self.assertIn("Telegram недоступен", cm.exception.detail)
        for secret in ("S3cretPw", "user1", "AAEhBP0av28"):
            self.assertNotIn(secret, cm.exception.detail)

    def test_check_proxy_ok(self):
        with mock.patch.object(telegram, "get_me", return_value={"username": "my_bot"}):
            out = self._call(api.telegram_check_proxy)
        self.assertEqual(out["message"], "бот @my_bot доступен")


class TempProxyTests(unittest.TestCase):
    SAVED = "http://saved.example:3128"
    NEW = "socks5://newuser:NewPw777@10.9.9.9:1080"

    def setUp(self):
        telegram.set_proxy(self.SAVED)
        self.seen = []
        self.db = FakeDb()

    def tearDown(self):
        telegram.set_proxy("")

    def _call(self, fn, body=None):
        def fake_get_me(token):
            self.seen.append(telegram.get_proxy())
            return {"username": "my_bot"}

        def fake_detect(token):
            self.seen.append(telegram.get_proxy())
            return []

        with mock.patch.object(subwatch, "bot_token", mock.AsyncMock(return_value=TOKEN)),                 mock.patch.object(subwatch, "apply_telegram_proxy", mock.AsyncMock()),                 mock.patch.object(telegram, "get_me", fake_get_me),                 mock.patch.object(telegram, "detect_chats", fake_detect):
            return run(fn("u", self.db, body))

    def test_temp_proxy_used_and_not_saved(self):
        out = self._call(api.telegram_check_proxy, TelegramProbeIn(proxy=self.NEW))
        self.assertEqual(self.seen, [self.NEW])
        self.assertEqual(telegram.get_proxy(), self.SAVED)   # глобальный не тронут
        self.assertEqual(self.db.rows, {})
        for secret in ("NewPw777", "newuser"):
            self.assertNotIn(secret, repr(out))

    def test_detect_uses_temp_proxy(self):
        self._call(api.detect_chat, TelegramProbeIn(proxy=self.NEW))
        self.assertEqual(self.seen, [self.NEW])
        self.assertEqual(telegram.get_proxy(), self.SAVED)

    def test_no_body_uses_saved(self):
        self._call(api.telegram_check_proxy, None)
        self._call(api.detect_chat, TelegramProbeIn())
        self.assertEqual(self.seen, [self.SAVED, self.SAVED])

    def test_empty_proxy_means_direct(self):
        self._call(api.telegram_check_proxy, TelegramProbeIn(proxy=""))
        self.assertEqual(self.seen, [""])

    def test_bad_proxy_422_without_echo(self):
        for fn in (api.telegram_check_proxy, api.detect_chat):
            with self.assertRaises(HTTPException) as cm:
                self._call(fn, TelegramProbeIn(proxy="ftp://user:pw9999@h:1"))
            self.assertEqual(cm.exception.status_code, 422)
            self.assertNotIn("pw9999", cm.exception.detail)
        self.assertEqual(self.seen, [])

    def test_error_text_scrubs_temp_proxy(self):
        def boom(token):
            raise telegram.TelegramError(telegram.scrub(f"таймаут {self.NEW}", token))
        with mock.patch.object(subwatch, "bot_token", mock.AsyncMock(return_value=TOKEN)),                 mock.patch.object(subwatch, "apply_telegram_proxy", mock.AsyncMock()),                 mock.patch.object(telegram, "get_me", boom):
            with self.assertRaises(HTTPException) as cm:
                run(api.telegram_check_proxy("u", FakeDb(), TelegramProbeIn(proxy=self.NEW)))
        self.assertIn("через прокси", cm.exception.detail)
        for secret in ("NewPw777", "newuser"):
            self.assertNotIn(secret, cm.exception.detail)


class StorageTests(unittest.TestCase):
    def tearDown(self):
        telegram.set_proxy("")

    def _save(self, db, **fields):
        body = SubwatchConfigUpdate(**fields)
        with mock.patch.object(subwatch, "get_config", mock.AsyncMock(return_value={})), \
                mock.patch.object(subwatch, "save_config", mock.AsyncMock()), \
                mock.patch.object(api, "_status", mock.AsyncMock(return_value="ok")):
            return run(api.update_settings(body, "u", db))

    def test_proxy_stored_encrypted_and_applied(self):
        db = FakeDb()
        self._save(db, telegram_proxy=PROXY)
        enc = db.rows[subwatch.TG_PROXY_KEY].value_enc
        self.assertNotIn("S3cretPw", enc)
        self.assertEqual(decrypt_secret(enc), PROXY)
        self.assertEqual(telegram.get_proxy(), PROXY)

    def test_empty_string_erases(self):
        db = FakeDb()
        self._save(db, telegram_proxy=PROXY)
        self._save(db, telegram_proxy="")
        self.assertIsNone(db.rows[subwatch.TG_PROXY_KEY].value_enc)
        self.assertEqual(telegram.get_proxy(), "")

    def test_bad_proxy_rejected_without_echo(self):
        db = FakeDb()
        with self.assertRaises(HTTPException) as cm:
            self._save(db, telegram_proxy="ftp://user:pw9999@h:1")
        self.assertNotIn("pw9999", cm.exception.detail)

    def test_config_out_has_flag_and_masked_only(self):
        db = FakeDb()
        self._save(db, telegram_proxy=PROXY)
        with mock.patch.object(subwatch, "bot_token", mock.AsyncMock(return_value="")):
            out = run(api._config_out(db, {}))
        dumped = out.model_dump_json()
        self.assertTrue(out.proxy_set)
        self.assertEqual(out.proxy_masked, "socks5://10.0.0.5:1080")
        self.assertNotIn("S3cretPw", dumped)
        self.assertNotIn("user1", dumped)


if __name__ == "__main__":
    unittest.main()
