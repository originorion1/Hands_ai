"""Offline end-to-end failure injection for veth-dependent rollback retention."""

import contextlib
import copy
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("orion_veth_acceptance", BASE / "apply-host-profile-v2.py")
HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOST)
FIXTURE = json.loads((BASE / "veth_ip_link_representative.json").read_text())


class FullCreationRollbackAcceptance(unittest.TestCase):
    def test_identity_failure_retains_full_dependency_set_in_both_placements(self):
        for pending in (True, False):
            with self.subTest(pending=pending):
                self._exercise(pending)

    def _exercise(self, pending):
        order = []
        mutations = []
        state = {"moved": False, "checks": 0, "replaced": False, "consumed": False}
        before = FIXTURE["before_move"]
        after = FIXTURE["after_move"]
        snapshot = {"routes": {}, "docker": {"nftables": []}, "historical": "safe", "units": {}}

        def marker(_path, _digest, created):
            order.append("grant_marker")
            created.append(("grant_marker", Path("/fixture/consumed"), 1, "test-digest"))
            state["consumed"] = True

        def namespace(created):
            order.append("namespace")
            created.append(("namespace", "net:[123]", 10))
            return "net:[123]"

        def directory(path, uid, gid, mode, created):
            order.append("resolver_dir")
            created.append(("dir", path, 20, uid, gid, mode))

        def file(path, data, uid, gid, mode, created):
            label = ({HOST.RESOLVER: "resolver_file", HOST.MARKER: "namespace_marker",
                      HOST.NETWORK_MARKER: "network_marker", HOST.UNITS / HOST.NEW_SOCKET: "socket_unit",
                      HOST.UNITS / HOST.NEW_TEMPLATE: "template_unit"})[path]
            order.append(label)
            created.append(("file", path, 30 + len(order), "test-digest", uid, gid, mode))

        def firewall(_address, created):
            for kind, value in (("nft_table", ("inet", HOST.FILTER, True, 41)),
                                ("nft_table", ("ip", HOST.NAT, False, 42)),
                                ("docker_rule", (43, {})), ("docker_rule", (44, {}))):
                order.append("namespace_filter" if value[0] == "inet" else
                             "host_nat" if kind == "nft_table" else "docker_rule")
                created.append((kind, *value))

        def run(args, **_kwargs):
            mutations.append(args)
            if args[:3] == [HOST.IP, "link", "add"]:
                order.append("veth")
            if args[:4] == [HOST.IP, "link", "set", HOST.P]:
                state["moved"] = True
            return ""

        def namespace_rows(*args):
            if args == ("link", "show"):
                return before["namespace_listing"]
            if args[0] == "-details":
                row = copy.deepcopy(after["pilot_namespace"])
                if state["checks"] >= 3:
                    row[0]["link"] = "drifted-peer"
                return row
            raise AssertionError(args)

        def host_rows(args):
            if args[-1] == HOST.P:
                row = copy.deepcopy(before["pilot_host"])
                if pending:
                    row[0]["link"] = "drifted-peer"
                return row
            row = copy.deepcopy(after["host"] if state["moved"] else before["host"])
            if state["replaced"]:
                row[0]["ifindex"] = 99
            return row

        original_verify = HOST.verify_veth_pair

        def verify(*args, **kwargs):
            state["checks"] += 1
            return original_verify(*args, **kwargs)

        original_rollback = HOST.rollback

        def rollback(created, address, **kwargs):
            # External actor replaces H after the last identity check and
            # before any possible name-based cleanup. The hold must win.
            state["replaced"] = True
            state["retained"] = copy.deepcopy(created)
            return original_rollback(created, address, **kwargs)

        def digest(path):
            return {HOST.RESOLVER: HOST.HOSTS_SHA, HOST.UNITS / HOST.OLD_SOCKET: HOST.OLD_SOCKET_SHA,
                    HOST.UNITS / HOST.OLD_TEMPLATE: HOST.OLD_TEMPLATE_SHA}[path]

        stderr = io.StringIO()
        with contextlib.ExitStack() as stack:
            values = {"verify_review": ({}, {}), "policy_tuple": ("192.0.2.1", "fixture.test"),
                      "proposed_units": (b"socket", b"template"),
                      "verify_grant": ({}, "a" * 64, Path("/fixture/consumed")),
                      "host_snapshot": snapshot, "create_namespace": namespace,
                      "one_shot_marker": marker, "create_dir": directory, "create_file": file,
                      "install_nft": firewall, "route_arrays": {}, "namespace_inode": "net:[123]",
                      "verify_routes": ({}, {}), "verify_nft": "digest",
                      "stopped_units": {}, "historical_fingerprint": "safe"}
            for name, value in values.items():
                if callable(value):
                    stack.enter_context(mock.patch.object(HOST, name, side_effect=value))
                else:
                    stack.enter_context(mock.patch.object(HOST, name, return_value=value))
            stack.enter_context(mock.patch.object(HOST, "link_index", side_effect=[11, 12]))
            stack.enter_context(mock.patch.object(HOST, "ns_json", side_effect=namespace_rows))
            stack.enter_context(mock.patch.object(HOST, "json_command", side_effect=host_rows))
            stack.enter_context(mock.patch.object(HOST, "veth_sysfs_indexes",
                                                  side_effect=lambda name, **_kw: (11, 12)
                                                  if name == HOST.H else (12, 11)))
            stack.enter_context(mock.patch.object(HOST, "verify_veth_pair", side_effect=verify))
            stack.enter_context(mock.patch.object(HOST, "rollback", side_effect=rollback))
            stack.enter_context(mock.patch.object(HOST, "run", side_effect=run))
            nft = stack.enter_context(mock.patch.object(HOST, "nft_run"))
            unlink = stack.enter_context(mock.patch.object(Path, "unlink"))
            rmdir = stack.enter_context(mock.patch.object(Path, "rmdir"))
            stack.enter_context(mock.patch.object(HOST, "sha", side_effect=digest))
            stack.enter_context(mock.patch.object(HOST, "absent", side_effect=lambda _path: not state["consumed"]))
            stack.enter_context(mock.patch.object(HOST, "rollback_proven",
                                                  side_effect=HOST.Blocked("RETAINED_FOR_MANUAL_RECOVERY")))
            stack.enter_context(mock.patch.object(HOST, "nft_entries", return_value=[]))
            stack.enter_context(mock.patch.object(HOST, "ns_ip"))
            stack.enter_context(mock.patch.object(HOST, "namespace_forwarding_zero"))
            stack.enter_context(mock.patch.object(HOST, "namespace_forwarding_read"))
            stack.enter_context(mock.patch.object(HOST, "verify_staged_files"))
            stack.enter_context(mock.patch.object(HOST, "systemd_state",
                                                  return_value={"UnitFileState": "static"}))
            stack.enter_context(mock.patch.object(HOST.os, "open", return_value=7))
            stack.enter_context(mock.patch.object(HOST.os, "close"))
            stack.enter_context(mock.patch.object(HOST.fcntl, "flock"))
            stack.enter_context(mock.patch.object(HOST.signal, "signal"))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            result = HOST.apply()
            nft.assert_not_called()
            unlink.assert_not_called()
            rmdir.assert_not_called()

        expected = ["grant_marker", "namespace", "resolver_dir", "resolver_file",
                    "namespace_filter", "host_nat", "docker_rule", "docker_rule", "veth"]
        if not pending:
            expected += ["namespace_marker", "network_marker", "socket_unit", "template_unit"]
        self.assertEqual(order, expected)
        self.assertTrue(state["replaced"])
        self.assertEqual(host_rows([HOST.H])[0]["ifindex"], 99)
        self.assertIn(("namespace", "net:[123]", 10), state["retained"])
        self.assertIn(("nft_table", "inet", HOST.FILTER, True, 41), state["retained"])
        self.assertIn(("nft_table", "ip", HOST.NAT, False, 42), state["retained"])
        kinds = [item["kind"] if isinstance(item, dict) else item[0] for item in state["retained"]]
        self.assertEqual(kinds.count("docker_rule"), 2)
        self.assertEqual(kinds.count("file"), 1 if pending else 5)
        self.assertEqual(state["checks"], 1 if pending else 3)
        self.assertEqual(result, 2)
        self.assertFalse(any("delete" in args for args in mutations), mutations)
        report = json.loads(stderr.getvalue())
        self.assertEqual(report["status"], "ROLLBACK_INCOMPLETE")
        self.assertEqual(report["cause"], "VETH_PAIR_IDENTITY")
        self.assertEqual(report["recovery_action"], "PRESERVE_RESOURCES_AND_REQUEST_MANUAL_REVIEW")
        self.assertEqual(report["retained_resource_categories"],
                         (["veth_pending", "docker_rule", "nft_table", "file", "dir",
                           "namespace", "grant_marker", "cleanup_unverified"] if pending else
                          ["file", "veth", "docker_rule", "nft_table", "dir",
                           "namespace", "grant_marker", "cleanup_unverified"]))
        self.assertTrue(report["grant_consumed"])
        self.assertNotIn("/fixture/", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
