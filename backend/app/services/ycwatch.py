"""Следилка за облаком Yandex Cloud, где живёт страница подписки.

01.10.2026 sub.vupen.fun отдавал 504: баланс облака ушёл в минус, Яндекс
остановил ВМ с Remnawave Subscription Page, и узнали мы об этом от клиентов.
Следилка за подпиской такое видит, но уже по факту. Здесь — заранее: раз в
час смотрим баланс платёжного аккаунта и статус самой ВМ.

Правила, чтобы чат не превратился в шум:
  • «мало денег» — баланс ниже порога: сообщение один раз, напоминание редко;
  • «авария» — баланс ушёл в ноль/минус, аккаунт неактивен или ВМ не RUNNING:
    сообщение сразу, пока не починили — напоминание раз в час;
  • облако не отвечает (истёк ключ, отняли роль) — тревога с N-го провала
    подряд: одна осечка — это сеть моргнула;
  • сообщение — только на переломах: стало хуже, стало лучше.

В облаке только читаем: служебный аккаунт имеет роли compute.viewer и
billing.accounts.viewer, ни запускать ВМ, ни трогать биллинг он не может —
и этот модуль не пытается.

Ключ СА (authorized key) лежит в app_settings зашифрованным, как токен бота.
Загрузить его, не светя в командной строке:

    docker compose exec -T api python -m app.services.ycwatch set-key < key.json
    docker compose exec -T api python -m app.services.ycwatch check
"""
from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting, YcCheck
from app.services import subwatch
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.telegram import send_message

log = logging.getLogger(__name__)

CONFIG_KEY = "ycwatch_config"
STATE_KEY = "ycwatch_state"
SA_KEY = "ycwatch_sa_key"

IAM_URL = "https://iam.api.cloud.yandex.net/iam/v1/tokens"
BILLING_URL = "https://billing.api.cloud.yandex.net/billing/v1/billingAccounts/"
COMPUTE_URL = "https://compute.api.cloud.yandex.net/compute/v1/instances"
TIMEOUT = 30.0
KEEP_DAYS = 30

# Облако не ответило столько раз подряд — значит, не сеть моргнула, а мы
# ослепли: ключ отозван, роль снята, аккаунт заблокирован.
FAIL_STREAK = 3
# Окно, по которому считаем скорость расхода. Короче полусуток — шум
# (Яндекс списывает неровно), длиннее трёх суток — не видно свежих перемен.
BURN_WINDOW = timedelta(hours=72)
BURN_MIN_SPAN = timedelta(hours=6)

OK = "ok"
LOW = "low"            # денег меньше порога, но всё работает
CRITICAL = "critical"  # баланс ≤ 0, аккаунт неактивен или ВМ не RUNNING
BLIND = "blind"        # облако не отвечает нам несколько опросов подряд

DEFAULTS = {
    "enabled": True,                          # ключа нет — следилка просто ждёт
    "interval_min": 60,                       # чаще не нужно: баланс меняется медленно
    "billing_account_id": "dn2bbmtdoelss4v09cva",   # account-677
    "folder_id": "b1gniirlj7p4k23gkl6i",            # каталог ВМ страницы подписки
    "instance_id": "",                        # пусто — все ВМ каталога
    "warn_rub": 300,                          # ниже — предупреждаем
    "remind_min": 60,                         # авария: напоминать не чаще
    "remind_low_hours": 24,                   # «мало денег» и «не отвечает»: не чаще
    "notify": True,
    "chat_id": "",                            # пусто — тот же чат, что у следилки за подпиской
}


class YcError(Exception):
    """Облако ответило не так. Текст безопасно показывать: секретов в нём нет."""


@dataclass
class Observation:
    ok: bool
    balance: float | None = None
    currency: str | None = None
    account_active: bool | None = None
    vms: list[dict] = field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def now() -> datetime:
    return _now()


# ── ключ и IAM-токен ─────────────────────────────────────────────────────────

def parse_key(raw: str) -> dict:
    """Разобрать authorized key из консоли Яндекса. Бросит ValueError, если это не он."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError("это не JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("это не JSON-объект")
    for name in ("id", "service_account_id", "private_key"):
        if not str(data.get(name) or "").strip():
            raise ValueError(f"в ключе нет поля {name}")
    _private_key(data["private_key"])   # проверим, что ключ читается
    return {k: data[k] for k in ("id", "service_account_id", "private_key")}


def _private_key(pem: str):
    # Яндекс кладёт перед PEM строку «PLEASE DO NOT REMOVE THIS LINE!…».
    start = pem.find("-----BEGIN")
    if start < 0:
        raise ValueError("в private_key нет PEM")
    try:
        return serialization.load_pem_private_key(pem[start:].encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise ValueError("private_key не читается") from exc


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwt(key: dict) -> str:
    now_ts = int(time.time())
    header = {"alg": "PS256", "typ": "JWT", "kid": key["id"]}
    payload = {"iss": key["service_account_id"], "aud": IAM_URL,
               "iat": now_ts, "exp": now_ts + 3600}
    signing_input = (_b64(json.dumps(header).encode()) + "." +
                     _b64(json.dumps(payload).encode())).encode()
    sig = _private_key(key["private_key"]).sign(
        signing_input,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=hashes.SHA256().digest_size),
        hashes.SHA256(),
    )
    return signing_input.decode() + "." + _b64(sig)


# IAM-токен живёт ~12 ч. Держим в памяти процесса: в базу его класть незачем,
# после перезапуска выпустим новый — это один запрос.
_iam: dict = {"key_id": None, "token": None, "until": None}


def _forget_token() -> None:
    _iam.update(key_id=None, token=None, until=None)


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None) -> dict:
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        try:
            detail = json.loads(detail).get("message") or detail
        except (ValueError, AttributeError):
            pass
        if exc.code == 401:
            raise YcError(f"ключ не принят (401): {detail}") from None
        if exc.code == 403:
            raise YcError(f"нет прав (403): {detail}") from None
        raise YcError(f"облако ответило {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise YcError(f"не достучались: {exc.reason}") from None
    except (TimeoutError, OSError) as exc:
        raise YcError(f"не достучались: {exc}") from None
    except ValueError:
        raise YcError("облако ответило не JSON") from None


def _iam_token(key: dict) -> str:
    now_dt = _now()
    if (_iam["token"] and _iam["key_id"] == key["id"]
            and _iam["until"] and now_dt < _iam["until"]):
        return _iam["token"]
    out = _request("POST", IAM_URL, body={"jwt": _jwt(key)})
    token = out.get("iamToken")
    if not token:
        raise YcError("IAM не выдал токен")
    until = now_dt + timedelta(hours=1)
    try:
        # Обновляем заранее, чтобы не поймать протухший токен посреди опроса.
        until = datetime.fromisoformat(str(out["expiresAt"])[:26].rstrip("Z") + "+00:00") \
            - timedelta(minutes=30)
    except (KeyError, ValueError):
        pass
    _iam.update(key_id=key["id"], token=token, until=until)
    return token


# ── один опрос ──────────────────────────────────────────────────────────────

def _vm(inst: dict) -> dict:
    ip = None
    for nic in inst.get("networkInterfaces") or []:
        ip = ((nic.get("primaryV4Address") or {}).get("oneToOneNat") or {}).get("address")
        if ip:
            break
    return {"id": inst.get("id"), "name": inst.get("name"),
            "status": inst.get("status") or "UNKNOWN", "ip": ip}


def probe(key: dict, cfg: dict) -> Observation:
    """Баланс и статус ВМ. Только GET-ы: в облаке ничего не меняем."""
    started = time.monotonic()
    obs = Observation(ok=False)
    try:
        token = _iam_token(key)
        acc = _request("GET", BILLING_URL + urllib.parse.quote(cfg["billing_account_id"]),
                       token=token)
        raw = acc.get("balance")
        obs.balance = float(raw) if raw not in (None, "") else None
        obs.currency = acc.get("currency")
        obs.account_active = acc.get("active")

        query = urllib.parse.urlencode({"folderId": cfg["folder_id"], "pageSize": 100})
        inst = _request("GET", f"{COMPUTE_URL}?{query}", token=token)
        vms = [_vm(i) for i in inst.get("instances") or []]
        want = (cfg.get("instance_id") or "").strip()
        if want:
            vms = [v for v in vms if v["id"] == want]
        obs.vms = vms
        if obs.balance is None:
            raise YcError("в ответе биллинга нет баланса")
        obs.ok = True
    except YcError as exc:
        if "401" in str(exc):
            _forget_token()
        obs.error = str(exc)
    except (ValueError, TypeError) as exc:
        obs.error = f"ключ или ответ не разобрать: {exc}"
    obs.duration_ms = int((time.monotonic() - started) * 1000)
    return obs


def assess(cfg: dict, obs: Observation) -> tuple[str, list[str]]:
    """Насколько всё плохо по удачному опросу и почему."""
    critical: list[str] = []
    if obs.balance is not None and obs.balance <= 0:
        critical.append(f"баланс {_money(obs.balance, obs.currency)} — Яндекс остановит ресурсы")
    if obs.account_active is False:
        critical.append("платёжный аккаунт неактивен")
    if not obs.vms:
        critical.append("ВМ в каталоге не найдена")
    for vm in obs.vms:
        if vm["status"] != "RUNNING":
            critical.append(f"ВМ {vm['name'] or vm['id']} в состоянии {vm['status']}")
    if critical:
        return CRITICAL, critical
    if obs.balance is not None and obs.balance < float(cfg.get("warn_rub") or 0):
        return LOW, [f"баланс {_money(obs.balance, obs.currency)} — ниже порога "
                     f"{_money(float(cfg['warn_rub']), obs.currency)}"]
    return OK, []


def burn_per_day(samples: list[tuple[datetime, float]]) -> float | None:
    """Сколько в сутки уходит, по ряду (время, баланс), старые первыми.

    Считаем только убывания: пополнение — скачок вверх, расходом не является.
    """
    if len(samples) < 2:
        return None
    end = samples[-1][0]
    window = [s for s in samples if end - s[0] <= BURN_WINDOW]
    if len(window) < 2 or window[-1][0] - window[0][0] < BURN_MIN_SPAN:
        return None
    spent = sum(max(0.0, a[1] - b[1]) for a, b in zip(window, window[1:]))
    hours = (window[-1][0] - window[0][0]).total_seconds() / 3600
    rate = spent / hours * 24
    return rate if rate > 0 else None


def _money(value: float, currency: str | None) -> str:
    sign = "−" if value < 0 else ""
    cur = "₽" if (currency or "RUB") == "RUB" else (currency or "")
    return f"{sign}{abs(value):,.2f}".replace(",", " ") + f" {cur}"


# ── настройки и состояние ────────────────────────────────────────────────────

async def get_config(db: AsyncSession) -> dict:
    return await subwatch._json_setting(db, CONFIG_KEY, DEFAULTS)


async def save_config(db: AsyncSession, cfg: dict) -> None:
    await subwatch._save_json(db, CONFIG_KEY, cfg)


async def get_state(db: AsyncSession) -> dict:
    return await subwatch._json_setting(
        db, STATE_KEY,
        {"level": None, "since": None, "fails": 0, "last_notified": None,
         "checked_at": None, "reasons": []})


async def get_key(db: AsyncSession) -> dict | None:
    row = await db.get(AppSetting, SA_KEY)
    if not row or not row.value_enc:
        return None
    try:
        return json.loads(decrypt_secret(row.value_enc))
    except Exception:  # noqa: BLE001 — сменился ключ шифрования
        return None


async def set_key(db: AsyncSession, raw: str | None) -> dict | None:
    """Сохранить ключ СА зашифрованным; пустая строка — стереть. Вернёт разобранный ключ."""
    row = await db.get(AppSetting, SA_KEY)
    if row is None:
        row = AppSetting(key=SA_KEY, value_enc=None)
        db.add(row)
        await db.flush()
    _forget_token()
    if not (raw or "").strip():
        row.value_enc = None
        return None
    key = parse_key(raw.strip())
    row.value_enc = encrypt_secret(json.dumps(key))
    return key


async def effective_chat(db: AsyncSession, cfg: dict) -> str:
    """Свой чат, а если не задан — общий, из следилки за подпиской."""
    return (cfg.get("chat_id") or "").strip() or \
        ((await subwatch.get_config(db)).get("chat_id") or "").strip()


async def samples(db: AsyncSession, since: timedelta = BURN_WINDOW) -> list[tuple[datetime, float]]:
    cutoff = _now() - since
    rows = await db.execute(
        select(YcCheck.checked_at, YcCheck.balance)
        .where(YcCheck.checked_at >= cutoff, YcCheck.ok.is_(True), YcCheck.balance.is_not(None))
        .order_by(YcCheck.checked_at.asc())
    )
    return [(r[0], float(r[1])) for r in rows.all()]


async def forecast(db: AsyncSession, balance: float | None) -> tuple[float | None, float | None]:
    """(расход в сутки, на сколько суток хватит) — если есть из чего считать."""
    rate = burn_per_day(await samples(db))
    if rate is None or balance is None:
        return rate, None
    return rate, max(0.0, balance / rate)


async def recent(db: AsyncSession, limit: int = 48) -> list[YcCheck]:
    rows = await db.execute(select(YcCheck).order_by(YcCheck.checked_at.desc()).limit(limit))
    return list(rows.scalars())


async def prune(db: AsyncSession) -> int:
    cutoff = _now() - timedelta(days=KEEP_DAYS)
    res = await db.execute(delete(YcCheck).where(YcCheck.checked_at < cutoff))
    await db.commit()
    return res.rowcount or 0


# ── тексты ──────────────────────────────────────────────────────────────────

def _vm_line(obs: Observation) -> str:
    if not obs.vms:
        return "ВМ: не найдена"
    return "ВМ: " + ", ".join(
        f"{html.escape(v['name'] or v['id'] or '?')}"
        f"{' (' + v['ip'] + ')' if v.get('ip') else ''} — {html.escape(v['status'])}"
        for v in obs.vms)


def _days_line(days: float | None, rate: float | None, currency: str | None) -> str | None:
    if days is None or rate is None:
        return None
    return f"Расход ~{_money(rate, currency)}/сут — хватит примерно на {days:.0f} сут."


def _text(level: str, prev: str | None, cfg: dict, obs: Observation, reasons: list[str],
          rate: float | None, days: float | None, fails: int) -> str:
    head = {
        CRITICAL: "🔴 <b>Yandex Cloud: страница подписки под угрозой</b>",
        LOW: "🟡 <b>Yandex Cloud: заканчиваются деньги</b>",
        BLIND: "⚪ <b>Yandex Cloud не отвечает рагенту</b>",
        OK: "🟢 <b>Yandex Cloud: всё в порядке</b>",
    }[level]
    lines = [head]
    if level == BLIND:
        lines.append(f"Не смогли узнать баланс и статус ВМ {fails} раз подряд.")
        lines.append(f"Ошибка: {html.escape(obs.error or '?')}")
        lines.append("Скорее всего истёк/отозван ключ СА или у него сняли роль. "
                     "Пока не починим — о деньгах и ВМ не узнаем.")
        return "\n".join(lines)
    if prev == BLIND:
        lines.append("Облако снова отвечает.")
    lines.extend(html.escape(r) for r in reasons)
    lines.append(f"Баланс: {_money(obs.balance or 0.0, obs.currency)}")
    lines.append(_vm_line(obs))
    days_line = _days_line(days, rate, obs.currency)
    if days_line:
        lines.append(days_line)
    if level == CRITICAL:
        lines.append("Пополнить баланс и, если ВМ остановлена, запустить её руками в консоли.")
    elif level == LOW:
        lines.append("Пополнить баланс, пока Яндекс не остановил ВМ (так было 01.10).")
    return "\n".join(x for x in lines if x)


# ── один цикл проверки ───────────────────────────────────────────────────────

def _delta_since(iso: str | None, now_dt: datetime) -> timedelta | None:
    if not iso:
        return None
    try:
        return now_dt - datetime.fromisoformat(iso)
    except ValueError:
        return None


def _remind_every(cfg: dict, level: str) -> timedelta | None:
    if level == CRITICAL:
        minutes = int(cfg.get("remind_min") or 0)
        return timedelta(minutes=minutes) if minutes > 0 else None
    if level in (LOW, BLIND):
        hours = int(cfg.get("remind_low_hours") or 0)
        return timedelta(hours=hours) if hours > 0 else None
    return None


def step(state: dict, cfg: dict, obs: Observation, now_dt: datetime) -> tuple[dict, str | None, list[str]]:
    """Новое состояние, уровень для сообщения (None — молчим) и причины.

    Чистая функция: решает, о чём говорить, а база и телеграм живут снаружи.
    """
    state = dict(state)
    prev = state.get("level")
    state["checked_at"] = now_dt.isoformat()

    if not obs.ok:
        fails = int(state.get("fails") or 0) + 1
        state["fails"] = fails
        state["error"] = obs.error
        if prev != BLIND and fails >= FAIL_STREAK:
            state.update(level=BLIND, since=now_dt.isoformat(),
                         last_notified=now_dt.isoformat(), reasons=[obs.error or "?"])
            return state, BLIND, []
        if prev == BLIND:
            state = _maybe_remind(state, cfg, BLIND, now_dt)
            return state, BLIND if state.pop("remind", False) else None, []
        return state, None, []

    level, reasons = assess(cfg, obs)
    state.update(fails=0, error=None, reasons=reasons)
    if level != prev:
        state.update(level=level, since=now_dt.isoformat())
        # Первый опрос после установки, и всё хорошо — говорить не о чем.
        if prev is None and level == OK:
            return state, None, reasons
        state["last_notified"] = now_dt.isoformat()
        return state, level, reasons
    if level == OK:
        return state, None, reasons
    state = _maybe_remind(state, cfg, level, now_dt)
    return state, level if state.pop("remind", False) else None, reasons


def _maybe_remind(state: dict, cfg: dict, level: str, now_dt: datetime) -> dict:
    every = _remind_every(cfg, level)
    if every is None:
        return state
    waited = _delta_since(state.get("last_notified"), now_dt)
    if waited is None or waited >= every:
        state = dict(state)
        state["last_notified"] = now_dt.isoformat()
        state["remind"] = True
    return state


async def check_once(db: AsyncSession, cfg: dict | None = None) -> dict:
    """Опросить облако, записать результат и, если надо, сказать в телеграм."""
    cfg = cfg or await get_config(db)
    key = await get_key(db)
    if key is None:
        obs = Observation(ok=False, error="ключ СА не задан")
    else:
        obs = await asyncio.to_thread(probe, key, cfg)

    db.add(YcCheck(ok=obs.ok, balance=obs.balance, currency=obs.currency,
                   account_active=obs.account_active, vms=obs.vms,
                   duration_ms=obs.duration_ms, error=obs.error))
    await db.flush()

    prev_state = await get_state(db)
    now_dt = _now()
    state, say, reasons = step(prev_state, cfg, obs, now_dt)

    rate, days = await forecast(db, obs.balance) if obs.ok else (None, None)
    notified: str | None = None
    if say is not None:
        text = _text(say, prev_state.get("level"), cfg, obs, reasons, rate, days,
                     int(state.get("fails") or 0))
        notified = await _notify(db, cfg, text)

    await subwatch._save_json(db, STATE_KEY, state)
    await db.commit()
    return {"obs": asdict(obs), "state": state, "notified": say, "notify_error": notified,
            "burn_per_day": rate, "days_left": days}


async def _notify(db: AsyncSession, cfg: dict, text: str) -> str | None:
    """Вернёт текст ошибки доставки, либо None если отправили (или не просили)."""
    if not cfg.get("notify"):
        return None
    token = await subwatch.bot_token(db)
    chat = await effective_chat(db, cfg)
    if not token or not chat:
        return "уведомления включены, но бот не настроен"
    err = await asyncio.to_thread(send_message, token, chat, text)
    if err:
        log.warning("ycwatch: уведомление не ушло: %s", err)
    return err


# ── фоновый цикл ─────────────────────────────────────────────────────────────

def _due(state: dict, cfg: dict, now_dt: datetime) -> bool:
    """Пора ли опрашивать. Считаем от прошлого опроса из базы, а не от старта
    процесса: перезапуск рагента не должен вызывать лишний поход в облако."""
    waited = _delta_since(state.get("checked_at"), now_dt)
    interval = timedelta(minutes=max(5, int(cfg.get("interval_min") or 60)))
    return waited is None or waited >= interval


async def run_ycwatch() -> None:
    """Раз в минуту смотрит, не пора ли; облако трогает раз в interval_min."""
    from app.db import SessionLocal

    await asyncio.sleep(30)
    ticks = 0
    while True:
        try:
            async with SessionLocal() as db:
                cfg = await get_config(db)
                if cfg.get("enabled") and await get_key(db) is not None \
                        and _due(await get_state(db), cfg, _now()):
                    out = await check_once(db, cfg)
                    obs = out["obs"]
                    if obs["ok"]:
                        log.info("ycwatch: баланс %s, ВМ %s, уровень %s",
                                 obs["balance"],
                                 ",".join(v["status"] for v in obs["vms"]) or "нет",
                                 out["state"].get("level"))
                    else:
                        log.warning("ycwatch: облако не ответило: %s", obs["error"])
                    ticks += 1
                    if ticks % 24 == 0:
                        await prune(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("ycwatch: цикл упал")
        await asyncio.sleep(60)


# ── загрузка ключа из консоли ────────────────────────────────────────────────

async def _cli(argv: list[str]) -> int:
    from app.db import SessionLocal

    cmd = argv[1] if len(argv) > 1 else ""
    async with SessionLocal() as db:
        if cmd == "set-key":
            # Ключ — только через stdin: в аргументах он остался бы в истории и ps.
            raw = sys.stdin.read()
            try:
                key = await set_key(db, raw)
            except ValueError as exc:
                print(f"ключ не принят: {exc}", file=sys.stderr)
                return 1
            await db.commit()
            if key is None:
                print("ключ стёрт")
            else:
                print(f"ключ сохранён: key_id={key['id']} sa={key['service_account_id']}")
            return 0
        if cmd == "check":
            out = await check_once(db)
            obs = out["obs"]
            print(json.dumps({
                "ok": obs["ok"], "balance": obs["balance"], "currency": obs["currency"],
                "account_active": obs["account_active"], "vms": obs["vms"],
                "error": obs["error"], "level": out["state"].get("level"),
                "notified": out["notified"], "notify_error": out["notify_error"],
                "days_left": out["days_left"],
            }, ensure_ascii=False))
            return 0 if obs["ok"] else 2
    print("использование: python -m app.services.ycwatch set-key < key.json | check",
          file=sys.stderr)
    return 64


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(_cli(sys.argv)))
