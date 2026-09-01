"""Патч routing в профиле Remnawave для redirect probe-доменов на локальный stub."""

from __future__ import annotations

from collections.abc import Iterator

from app.services.remnawave_api import RemnawaveApiError, rw_api, rw_node_by_name

STUB_PORT = 19081
PROBE_DOMAINS = [
    "full:cp.cloudflare.com",
    "domain:google.com",
    "domain:gstatic.com",
]


def patch_profile_for_node(node_name: str, redirect: str) -> Iterator[str]:
    """Добавляет outbound probe-stub и правила маршрутизации в активный профиль ноды."""
    rw_node = rw_node_by_name(node_name)
    profile_uuid = (rw_node.get("configProfile") or {}).get("activeConfigProfileUuid")
    if not profile_uuid:
        raise RemnawaveApiError(f"у ноды {node_name} нет activeConfigProfileUuid")

    prof = rw_api("/api/config-profiles/" + profile_uuid)
    if not isinstance(prof, dict):
        raise RemnawaveApiError("неожиданный ответ профиля")

    cfg = prof.get("config") or {}
    outbounds = list(cfg.get("outbounds") or [])
    tags = {o.get("tag") for o in outbounds if isinstance(o, dict)}
    stub_ip = redirect.split(":")[0]

    probe_ob = {
        "tag": "probe-stub",
        "protocol": "freedom",
        "settings": {
            "redirect": redirect,
            "finalRules": [
                {
                    "action": "allow",
                    "network": "tcp",
                    "ip": [stub_ip if "/" in stub_ip else f"{stub_ip}/32"],
                    "port": str(STUB_PORT),
                }
            ],
        },
    }
    outbounds = [o for o in outbounds if o.get("tag") != "probe-stub"]
    outbounds.append(probe_ob)

    rules = list((cfg.get("routing") or {}).get("rules") or [])
    rules = [
        r
        for r in rules
        if not (
            isinstance(r, dict)
            and r.get("outboundTag") in ("direct", "DIRECT", "probe-stub")
            and any(
                "cloudflare" in str(d).lower()
                or "google.com" in str(d).lower()
                or "gstatic.com" in str(d).lower()
                for d in (r.get("domain") or [])
            )
        )
    ]
    rules.insert(
        0,
        {
            "type": "field",
            "domain": PROBE_DOMAINS,
            "outboundTag": "probe-stub",
        },
    )
    direct_tag = "DIRECT" if "DIRECT" in tags else "direct"
    bypass = {
        "type": "field",
        "ip": [stub_ip],
        "port": str(STUB_PORT),
        "outboundTag": direct_tag,
    }
    insert_at = len(rules)
    for i, r in enumerate(rules):
        dom = r.get("domain") or []
        ips = r.get("ip") or []
        if r.get("outboundTag") in ("block", "block_rst") and (
            "geoip:private" in ips or "geosite:private" in dom
        ):
            insert_at = i
            break
    rules.insert(insert_at, bypass)

    cfg["outbounds"] = outbounds
    cfg.setdefault("routing", {})["rules"] = rules

    yield f"→ Патч профиля {prof.get('name') or profile_uuid}…"
    rw_api(
        "/api/config-profiles",
        "PATCH",
        {"uuid": profile_uuid, "name": prof.get("name"), "config": cfg},
    )
    yield f"✓ routing: probe domains → {redirect}"

    node_uuid = rw_node.get("uuid")
    if node_uuid:
        try:
            rw_api(f"/api/nodes/{node_uuid}/actions/restart", "POST", {"forceRestart": True})
            yield f"✓ restart {node_name}"
        except RemnawaveApiError as exc:
            yield f"⚠ restart ноды: {exc.message}"
