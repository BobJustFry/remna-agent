"""Тесты разбора панели в проверках. Сеть не трогают — только логика."""
import unittest
from unittest import mock

from app.services import audit


def _profile(name, tag, inbound_uuid, stream, nodes=("node-1",)):
    return {
        "name": name,
        "uuid": f"prof-{name}",
        "nodes": [{"name": n} for n in nodes],
        "inbounds": [{"uuid": inbound_uuid, "tag": tag}],
        "config": {"inbounds": [{"tag": tag, "streamSettings": stream}]},
    }


def _host(remark, inbound_uuid, **kw):
    h = {
        "remark": remark,
        "address": kw.pop("address", "10.0.0.1"),
        "port": kw.pop("port", 443),
        "isDisabled": kw.pop("isDisabled", False),
        "nodes": kw.pop("nodes", ["node-1"]),
        "inbound": ({"configProfileInboundUuid": inbound_uuid} if inbound_uuid else None),
    }
    h.update(kw)
    return h


REALITY = {
    "network": "xhttp",
    "security": "reality",
    "xhttpSettings": {"host": "old.example.com", "mode": "packet-up"},
    "realitySettings": {"target": "new.example.com:443", "serverNames": ["new.example.com"]},
}


class UnwrapTests(unittest.TestCase):
    def test_list_passes_through(self) -> None:
        self.assertEqual(audit._unwrap([1, 2]), [1, 2])

    def test_picks_known_key(self) -> None:
        self.assertEqual(audit._unwrap({"hosts": [{"a": 1}]}), [{"a": 1}])

    def test_garbage_gives_empty(self) -> None:
        self.assertEqual(audit._unwrap(None), [])
        self.assertEqual(audit._unwrap({"nope": 1}), [])


class InboundIndexTests(unittest.TestCase):
    def test_maps_uuid_to_inbound_and_nodes(self) -> None:
        idx = audit._inbound_index([_profile("P", "TAG", "u1", REALITY)])
        self.assertIn("u1", idx)
        self.assertEqual(idx["u1"]["tag"], "TAG")
        self.assertEqual(idx["u1"]["nodes"], ["node-1"])

    def test_skips_meta_without_matching_config(self) -> None:
        prof = _profile("P", "TAG", "u1", REALITY)
        prof["config"]["inbounds"] = []          # тега в конфиге нет
        self.assertEqual(audit._inbound_index([prof]), {})


class ConfigDriftTests(unittest.TestCase):
    def _run(self, hosts, profiles):
        with mock.patch.object(audit, "_panel", return_value=(hosts, profiles, [])):
            return audit.check_config_drift()

    def test_stale_xhttp_host_is_only_information(self) -> None:
        """Клиент и сервер согласованы — это след прежней смены dest, не поломка."""
        res = self._run([_host("H", "u1", host="old.example.com")],
                        [_profile("P", "TAG", "u1", REALITY)])
        hit = [f for f in res.findings if "Host у xhttp остался" in f.title]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].severity, "info")

    def test_host_and_inbound_disagreeing_is_critical(self) -> None:
        """А вот когда панель отдаёт один Host, а сервер ждёт другой — хост мёртв."""
        res = self._run([_host("H", "u1", host="third.example.com")],
                        [_profile("P", "TAG", "u1", REALITY)])
        hit = [f for f in res.findings if "разошлись" in f.title]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].severity, "crit")

    def test_host_sni_overriding_profile_is_critical(self) -> None:
        res = self._run([_host("H", "u1", sni="old.example.com")],
                        [_profile("P", "TAG", "u1", REALITY)])
        crit = [f for f in res.findings if f.severity == "crit"]
        self.assertEqual(len(crit), 1)
        self.assertEqual(crit[0].title, "SNI хоста перекрывает профиль")

    def test_disabled_host_raises_nothing(self) -> None:
        """Выключенный хост клиентам не выдаётся — тревожить по нему нельзя."""
        res = self._run([_host("H", None, isDisabled=True, sni="whatever")],
                        [_profile("P", "TAG", "u1", REALITY)])
        self.assertEqual([f for f in res.findings if f.severity == "crit"], [])

    def test_enabled_host_without_inbound_is_critical(self) -> None:
        res = self._run([_host("H", None)], [_profile("P", "TAG", "u1", REALITY)])
        crit = [f for f in res.findings if f.severity == "crit"]
        self.assertEqual(len(crit), 1)
        self.assertEqual(crit[0].title, "У хоста не задан инбаунд")

    def test_orphan_inbounds_collapse_into_one_finding(self) -> None:
        profiles = [_profile("A", "T1", "u1", REALITY), _profile("B", "T2", "u2", REALITY)]
        res = self._run([], profiles)
        orphan = [f for f in res.findings if "не смотрит ни один хост" in f.title]
        self.assertEqual(len(orphan), 1)
        self.assertIn("A/T1", orphan[0].detail)
        self.assertIn("B/T2", orphan[0].detail)

    def test_shared_dest_is_reported_once(self) -> None:
        profiles = [_profile("A", "T1", "u1", REALITY), _profile("B", "T2", "u2", REALITY)]
        res = self._run([_host("H1", "u1"), _host("H2", "u2")], profiles)
        shared = [f for f in res.findings if "Один dest" in f.title]
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0].target, "new.example.com")


class CertTargetTests(unittest.TestCase):
    def test_skips_reality_udp_and_placeholders(self) -> None:
        udp = {"network": "hysteria", "security": "tls"}
        tls = {"network": "xhttp", "security": "none"}
        hosts = [
            _host("reality", "u1"),
            _host("hysteria", "u2"),
            _host("баннер", "u3", address="1.1.1.1", port=1234, sni="do.not.use"),
        ]
        profiles = [
            _profile("A", "T1", "u1", REALITY),
            _profile("B", "T2", "u2", udp),
            _profile("C", "T3", "u3", tls),
        ]
        seen = []
        with mock.patch.object(audit, "_panel", return_value=(hosts, profiles, [])), \
                mock.patch.object(audit, "_cert_info", side_effect=lambda *a: seen.append(a)):
            res = audit.check_certs()
        self.assertEqual(seen, [])           # ни один из трёх проверяться не должен
        self.assertEqual(res.checked, 0)


class RunTests(unittest.TestCase):
    def test_unknown_check_raises(self) -> None:
        with self.assertRaises(KeyError):
            audit.run("нет-такой-проверки")

    def test_failure_becomes_result_not_exception(self) -> None:
        with mock.patch.dict(audit.CHECKS, {"boom": {"title": "", "what": "", "why": "",
                                                     "run": lambda: 1 / 0}}):
            res = audit.run("boom")
        self.assertIsNotNone(res.error)
        self.assertIn("ZeroDivisionError", res.error)


class PanelPermissionTests(unittest.TestCase):
    """Право на конфиг-профили может быть не выдано — раздел не должен от этого умирать."""

    def _rw(self, forbid_profiles=True):
        def call(path, *a, **kw):
            if path == "/api/config-profiles" and forbid_profiles:
                raise audit.RemnawaveApiError('Remnawave API 403: {"message":"Forbidden resource"}')
            return []
        return call

    def test_profiles_optional_when_check_does_not_need_them(self) -> None:
        with mock.patch.object(audit, "rw_api", side_effect=self._rw()):
            hosts, profiles, nodes = audit._panel(profiles_required=False)
        self.assertEqual(profiles, [])

    def test_required_profiles_give_an_actionable_message(self) -> None:
        with mock.patch.object(audit, "rw_api", side_effect=self._rw()):
            with self.assertRaises(audit.RemnawaveApiError) as ctx:
                audit._panel()
        self.assertIn("Config Profiles", ctx.exception.message)

    def test_cdn_check_survives_missing_profiles(self) -> None:
        with mock.patch.object(audit, "rw_api", side_effect=self._rw()):
            res = audit.run("cdn_alive")
        self.assertIsNone(res.error)

    def test_other_errors_are_not_swallowed(self) -> None:
        def call(path, *a, **kw):
            if path == "/api/config-profiles":
                raise audit.RemnawaveApiError("Remnawave API 500: oops")
            return []
        with mock.patch.object(audit, "rw_api", side_effect=call):
            with self.assertRaises(audit.RemnawaveApiError) as ctx:
                audit._panel(profiles_required=False)
        self.assertIn("500", ctx.exception.message)


class PinnedEdgeTests(unittest.TestCase):
    """Выдача CDN геозависима: не попасть в неё — ещё не поломка."""

    HOSTS = [{"remark": "DE", "address": "185.141.224.48", "port": 443,
              "isDisabled": False, "sni": "abc.a.trbcdn.net", "inbound": None}]

    def _run(self, reachable):
        with mock.patch.object(audit, "_panel", return_value=(self.HOSTS, [], [])),                 mock.patch.object(audit, "_resolve", return_value=["151.236.110.232"]),                 mock.patch.object(audit, "_tls_reachable", return_value=reachable):
            return audit.check_cdn_alive()

    def test_live_edge_out_of_the_pool_is_only_information(self) -> None:
        res = self._run(True)
        hit = [f for f in res.findings if "отвечает, но резолвер" in f.title]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].severity, "info")

    def test_dead_edge_out_of_the_pool_is_critical(self) -> None:
        res = self._run(False)
        hit = [f for f in res.findings if "не отвечает" in f.title]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].severity, "crit")


if __name__ == "__main__":
    unittest.main()
