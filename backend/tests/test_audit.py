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

    def test_finds_stale_xhttp_host(self) -> None:
        res = self._run([_host("H", "u1")], [_profile("P", "TAG", "u1", REALITY)])
        titles = [f.title for f in res.findings]
        self.assertIn("Host у xhttp не совпадает с SNI инбаунда", titles)

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


if __name__ == "__main__":
    unittest.main()
