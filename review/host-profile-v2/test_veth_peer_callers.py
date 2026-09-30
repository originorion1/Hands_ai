"""Offline caller-level veth checks; every host-facing operation is mocked."""

import contextlib
import copy
import importlib.util
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("orion_veth_peer_callers", HERE / "apply-host-profile-v2.py")
HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOST)
FIXTURE = json.loads((HERE / "veth_ip_link_representative.json").read_text())
BEFORE = FIXTURE["before_move"]
AFTER = FIXTURE["after_move"]
CAPTURE = HERE / "captured-veth"
RAW_SYSFS = json.loads((CAPTURE / "sysfs_indexes.json").read_text())
RAW_BEFORE = {"host": json.loads((CAPTURE / "before_host.json").read_text()),
              "pilot_host": json.loads((CAPTURE / "before_peer.json").read_text()),
              "namespace_listing": json.loads((CAPTURE / "before_namespace_listing.json").read_text())}
RAW_AFTER = {"host": json.loads((CAPTURE / "after_host.json").read_text()),
             "pilot_namespace": json.loads((CAPTURE / "after_peer.json").read_text()),
             "namespace_listing": json.loads((CAPTURE / "after_namespace_listing.json").read_text())}
DIRECT = HERE / "captured-veth-direct-nsid"
DIRECT_HOST = json.loads((DIRECT / "direct_host.json").read_text())
DIRECT_PEER = json.loads((DIRECT / "direct_peer.json").read_text())
DIRECT_HOST_LIST = json.loads((DIRECT / "after_host_listing.json").read_text())
DIRECT_NS_BEFORE = json.loads((DIRECT / "before_namespace_listing.json").read_text())
DIRECT_NS_AFTER = json.loads((DIRECT / "after_namespace_listing.json").read_text())
DIRECT_SYSFS = json.loads((DIRECT / "sysfs_indexes.json").read_text())
DIRECT_NSID = json.loads((DIRECT / "netnsid_resolution.json").read_text())
NSID_PAIR = (DIRECT_NSID["host_to_pinned_peer"], DIRECT_NSID["peer_to_pinned_host"])


class VethCallerTests(unittest.TestCase):
    def test_representative_arrays_have_named_peers_and_no_link_index(self):
        self.assertIn("Representative", FIXTURE["provenance"])
        for placement in (BEFORE, AFTER):
            peer_key = "pilot_host" if placement is BEFORE else "pilot_namespace"
            host, peer = placement["host"][0], placement[peer_key][0]
            self.assertEqual((host["link"], peer["link"]), (HOST.P, HOST.H))
            self.assertNotIn("link_index", host)
            self.assertNotIn("link_index", peer)
            self.assertEqual((host["ifindex"], peer["ifindex"]), (11, 12))

    def test_create_veth_records_intent_before_uncertain_command(self):
        for failure, code in ((HOST.Blocked("COMMAND_OUTCOME_UNCERTAIN"),
                               "COMMAND_OUTCOME_UNCERTAIN"),
                              (subprocess.TimeoutExpired(HOST.IP, 15),
                               "VETH_CREATE_COMMAND_UNCERTAIN"),
                              (OSError("unavailable"), "VETH_CREATE_COMMAND_UNCERTAIN")):
            with self.subTest(code=code, failure=type(failure).__name__):
                created = []
                def uncertain(args, failure=failure, created=created):
                    self.assertEqual(args, [HOST.IP, "link", "add", HOST.H, "type", "veth",
                                            "peer", "name", HOST.P, "netns", HOST.NS])
                    self.assertEqual(created, [{"kind": "veth_pending",
                                                "namespace_inode": "net:[123]",
                                                "host_idx": None, "pilot_idx": None}])
                    raise failure

                with mock.patch.object(HOST, "run", side_effect=uncertain) as run, \
                     mock.patch.object(HOST, "link_index") as indexes, \
                     self.assertRaisesRegex(HOST.Blocked, code):
                    HOST.create_veth("net:[123]", created)
                run.assert_called_once()
                indexes.assert_not_called()
                self.assertEqual(HOST.rollback(created, "192.0.2.1"), ["veth_pending"])

    def test_create_veth_drift_leaves_pending_record_and_does_not_move(self):
        created = []
        drifted = copy.deepcopy(DIRECT_PEER)
        drifted[0]["link_index"] = 999

        def host_json(args):
            return DIRECT_HOST_LIST if args == [HOST.IP, "-j", "link", "show"] else DIRECT_HOST

        def ns_json(*args):
            return DIRECT_NS_AFTER if args == ("link", "show") else drifted

        with mock.patch.object(HOST, "run", return_value="") as run, \
             mock.patch.object(HOST, "link_index", side_effect=[2, 2]), \
             mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command", side_effect=host_json), \
             mock.patch.object(HOST, "ns_json", side_effect=ns_json), \
             mock.patch.object(HOST, "veth_sysfs_indexes"), \
             mock.patch.object(HOST.veth_nsid, "resolve_pair", return_value=NSID_PAIR), \
             self.assertRaises(HOST.Blocked):
            HOST.create_veth("net:[123]", created)
        self.assertEqual(created, [{"kind": "veth_pending", "namespace_inode": "net:[123]",
                                    "host_idx": 2, "pilot_idx": 2}])
        self.assertEqual(len(run.call_args_list), 1)

    def test_create_veth_uses_raw_capture_and_reciprocal_sysfs_evidence(self):
        host_idx = DIRECT_HOST[0]["ifindex"]
        peer_idx = DIRECT_PEER[0]["ifindex"]
        created = []

        def host_json(args):
            return DIRECT_HOST_LIST if args == [HOST.IP, "-j", "link", "show"] else DIRECT_HOST

        def ns_json(*args):
            return DIRECT_NS_AFTER if args == ("link", "show") else DIRECT_PEER

        def add(args):
            if args[:4] == [HOST.IP, "netns", "exec", HOST.NS]:
                field = Path(args[-1]).name
                return DIRECT_SYSFS["peer"][field]
            self.assertEqual(created[0]["kind"], "veth_pending")
            self.assertEqual(args, [HOST.IP, "link", "add", HOST.H, "type", "veth",
                                    "peer", "name", HOST.P, "netns", HOST.NS])
            return ""

        with mock.patch.object(HOST, "run", side_effect=add) as run, \
             mock.patch.object(HOST, "link_index", side_effect=[host_idx, peer_idx]) as links, \
             mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command", side_effect=host_json), \
             mock.patch.object(HOST, "ns_json", side_effect=ns_json), \
             mock.patch.object(HOST.veth_nsid, "resolve_pair", return_value=NSID_PAIR) as nsid, \
             mock.patch.object(HOST.Path, "read_text",
                               side_effect=[DIRECT_SYSFS["host"][key]
                                            for key in ("ifindex", "iflink")]) as reads:
            self.assertEqual(HOST.create_veth("net:[123]", created), (host_idx, peer_idx))
        self.assertEqual(created, [("veth", host_idx, peer_idx, "net:[123]")])
        self.assertEqual(links.call_args_list,
                         [mock.call(HOST.H), mock.call(HOST.P, namespace=True)])
        self.assertEqual(reads.call_count, 2)
        nsid.assert_called_once_with(HOST.NS_PATH, "net:[123]")
        self.assertEqual(len(run.call_args_list), 3)  # add, then two namespace sysfs reads

    def _apply_through_direct_creation(self, *, command_uncertain=False, peer_drift=False,
                                       replace_before_rollback=False, fault=None):
        """Exercise actual create_veth and apply rollback with direct raw evidence."""
        host_idx = DIRECT_HOST[0]["ifindex"]
        peer_idx = DIRECT_PEER[0]["ifindex"]
        before = {"routes": {}, "docker": {"nftables": []}, "historical": "x", "units": {}}
        peer = copy.deepcopy(DIRECT_PEER)
        if peer_drift:
            peer[0]["link_index"] = 999
        if fault in ("disconnected_pairs", "wrong_peer_nsid"):
            peer[0]["link_netnsid"] = NSID_PAIR[1] + 2
        if fault == "missing_peer_nsid":
            peer[0].pop("link_netnsid")
        calls = []
        stderr = io.StringIO()
        state = {"host": copy.deepcopy(DIRECT_HOST), "created": False,
                 "intent_at_add": False, "journal": None}
        if fault in ("disconnected_pairs", "wrong_host_nsid"):
            state["host"][0]["link_netnsid"] = NSID_PAIR[0] + 1
        if fault == "missing_host_nsid":
            state["host"][0].pop("link_netnsid")

        def host_json(args):
            if args == [HOST.IP, "-j", "link", "show"]:
                listing = copy.deepcopy(DIRECT_HOST_LIST)
                if fault == "host_peer_collision":
                    listing.append({"ifname": HOST.P, "ifindex": 77})
                if state["host"][0]["ifindex"] != host_idx:
                    next(row for row in listing if row["ifname"] == HOST.H)["ifindex"] = 99
                return listing
            return copy.deepcopy(state["host"])

        def namespace_json(*args):
            if args == ("link", "show"):
                if state["created"] and fault == "missing_namespace_peer":
                    return DIRECT_NS_BEFORE
                if state["created"] and fault == "duplicate_namespace_peer":
                    return [*DIRECT_NS_AFTER, copy.deepcopy(DIRECT_NS_AFTER[-1])]
                if state["created"] and fault == "malformed_namespace_listing":
                    return {"unexpected": "object"}
                return DIRECT_NS_AFTER if state["created"] else DIRECT_NS_BEFORE
            return peer

        def checked_sysfs(name, *, namespace=False):
            if fault == "missing_sysfs" and name == HOST.P:
                raise HOST.Blocked("VETH_SYSFS_READ_FAILED")
            side = "host" if name == HOST.H else "peer"
            values = DIRECT_SYSFS[side]
            return tuple(int(values[key]) for key in ("ifindex", "iflink"))

        def fake_run(args, **_kwargs):
            calls.append(args)
            if args[:3] == [HOST.IP, "link", "add"]:
                state["intent_at_add"] = any(item.get("kind") == "veth_pending"
                                             for item in state["journal"]
                                             if isinstance(item, dict))
                state["created"] = True
                if command_uncertain:
                    if replace_before_rollback:
                        state["host"][0]["ifindex"] = 99
                    raise HOST.Blocked("COMMAND_OUTCOME_UNCERTAIN")
            if args[:3] == [HOST.IP, "address", "add"]:
                if replace_before_rollback:
                    state["host"][0]["ifindex"] = 99
                raise HOST.Blocked("STOP_AFTER_DIRECT_CREATE")
            return ""

        created = []
        def namespace(journal):
            journal.append(("namespace", "net:[123]", 10))
            return "net:[123]"

        def marker(_path, _digest, journal):
            state["journal"] = journal
            journal.append(("grant_marker", Path("/fixture/used"), 1, "digest"))

        def record_dir(_path, _uid, _gid, _mode, journal):
            journal.append(("dir", Path("/fixture/resolver"), 2, 0, 0, 0o750))

        def record_file(_path, _data, _uid, _gid, _mode, journal):
            journal.append(("file", Path("/fixture/resolver/hosts"), 3, "digest", 0, 0, 0o640))

        def firewall(_address, journal):
            journal.append(("nft_table", "inet", HOST.FILTER, True, 41))
            journal.append(("nft_table", "ip", HOST.NAT, False, 42))

        with contextlib.ExitStack() as stack:
            for name, value in (
                ("verify_review", ({}, {})),
                ("policy_tuple", ("192.0.2.1", "fixture.test")),
                ("proposed_units", (b"", b"")),
                ("verify_grant", ({}, "a" * 64, Path("/fixture/used"))),
                ("host_snapshot", before),
                ("absent", True),
                ("route_arrays", {"ipv4_main": []}),
                ("sha", HOST.HOSTS_SHA),
            ):
                stack.enter_context(mock.patch.object(HOST, name, return_value=value))
            stack.enter_context(mock.patch.object(
                HOST, "namespace_inode",
                side_effect=lambda: "net:[999]" if fault == "namespace_drift" and
                state["created"] else "net:[123]"))
            for name, implementation in (
                ("one_shot_marker", marker), ("create_namespace", namespace),
                ("create_dir", record_dir), ("create_file", record_file),
                ("install_nft", firewall),
            ):
                stack.enter_context(mock.patch.object(HOST, name, side_effect=implementation))
            for name in ("ns_ip", "namespace_forwarding_zero"):
                stack.enter_context(mock.patch.object(HOST, name))
            stack.enter_context(mock.patch.object(HOST, "ns_json", side_effect=namespace_json))
            stack.enter_context(mock.patch.object(HOST, "json_command", side_effect=host_json))
            stack.enter_context(mock.patch.object(HOST, "link_index", side_effect=[host_idx, peer_idx]))
            sysfs = stack.enter_context(mock.patch.object(
                HOST, "veth_sysfs_indexes", side_effect=checked_sysfs))
            stack.enter_context(mock.patch.object(
                HOST.veth_nsid, "resolve_pair", return_value=NSID_PAIR,
                side_effect=(HOST.veth_nsid.NsidError("NSID_MAPPING_AMBIGUOUS")
                             if fault == "ambiguous_nsid_mapping" else
                             HOST.veth_nsid.NsidError("NSID_MAPPING_MISSING")
                             if fault == "missing_nsid_mapping" else None)))
            stack.enter_context(mock.patch.object(HOST, "run", side_effect=fake_run))
            stack.enter_context(mock.patch.object(HOST.os, "open", return_value=7))
            stack.enter_context(mock.patch.object(HOST.os, "close"))
            stack.enter_context(mock.patch.object(HOST.fcntl, "flock"))
            stack.enter_context(mock.patch.object(HOST.signal, "signal"))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            with mock.patch.object(HOST, "rollback", wraps=HOST.rollback) as rollback:
                result = HOST.apply()
                created = rollback.call_args.args[0]
            sysfs_calls = sysfs.call_args_list
        return (result, calls, sysfs_calls, json.loads(stderr.getvalue()),
                state["host"][0]["ifindex"], state["intent_at_add"], created)

    def test_apply_direct_creation_verifies_before_addressing(self):
        result, calls, reads, report, _index, intent, created = (
            self._apply_through_direct_creation())
        self.assertEqual(result, 2)
        self.assertTrue(intent)
        self.assertEqual(calls[0], [HOST.IP, "link", "add", HOST.H, "type", "veth",
                                    "peer", "name", HOST.P, "netns", HOST.NS])
        self.assertNotIn([HOST.IP, "link", "set", HOST.P, "netns", HOST.NS], calls)
        self.assertIn([HOST.IP, "address", "add", HOST.HOST_IP + "/30", "dev", HOST.H], calls)
        self.assertEqual(reads, [mock.call(HOST.H), mock.call(HOST.P, namespace=True)] * 2)
        self.assertIn(("veth", DIRECT_HOST[0]["ifindex"], DIRECT_PEER[0]["ifindex"],
                       "net:[123]"), created)
        self.assertEqual(report["status"], "ROLLBACK_INCOMPLETE")

    def test_apply_direct_peer_drift_blocks_before_addressing(self):
        result, calls, reads, report, _index, intent, created = (
            self._apply_through_direct_creation(peer_drift=True))
        self.assertEqual(result, 2)
        self.assertTrue(intent)
        self.assertEqual(len(calls), 1)
        self.assertEqual(reads, [])
        self.assertIn("veth_pending", report["rollback_unresolved_categories"])
        self.assertEqual(report["status"], "ROLLBACK_INCOMPLETE")
        self.assertTrue(any(isinstance(item, dict) and item["kind"] == "veth_pending"
                            for item in created))

    def test_disconnected_pairs_with_colliding_indexes_are_retained(self):
        self.assertEqual(DIRECT_HOST[0]["ifindex"], DIRECT_PEER[0]["ifindex"])
        for fault in ("disconnected_pairs", "wrong_host_nsid", "wrong_peer_nsid",
                      "missing_host_nsid", "missing_peer_nsid",
                      "missing_nsid_mapping", "ambiguous_nsid_mapping"):
            with self.subTest(fault=fault):
                result, calls, _reads, report, _index, intent, created = (
                    self._apply_through_direct_creation(fault=fault))
                self.assertEqual(result, 2)
                self.assertTrue(intent)
                self.assertEqual(report["cause"], "VETH_NSID_UNVERIFIED")
                self.assertEqual(report["status"], "ROLLBACK_INCOMPLETE")
                self.assertIn("veth_pending", report["rollback_unresolved_categories"])
                self.assertIn("namespace", report["rollback_unresolved_categories"])
                self.assertIn("nft_table", report["rollback_unresolved_categories"])
                self.assertTrue(any(isinstance(item, dict) and item["kind"] == "veth_pending"
                                    for item in created))
                self.assertEqual(len(calls), 1)
                self.assertFalse(any("delete" in args for args in calls))

    def test_apply_direct_placement_and_sysfs_uncertainty_never_delete(self):
        for fault in ("host_peer_collision", "missing_namespace_peer",
                      "duplicate_namespace_peer",
                      "malformed_namespace_listing", "namespace_drift", "missing_sysfs"):
            with self.subTest(fault=fault):
                result, calls, _reads, report, _index, intent, created = (
                    self._apply_through_direct_creation(fault=fault))
                self.assertTrue(intent)
                self.assertEqual(result, 2)
                self.assertEqual(report["status"], "ROLLBACK_INCOMPLETE")
                self.assertIn("veth_pending", report["rollback_unresolved_categories"])
                self.assertIn("nft_table", report["rollback_unresolved_categories"])
                self.assertIn("namespace", report["rollback_unresolved_categories"])
                self.assertIn(report["cause"], {"VETH_PAIR_PLACEMENT",
                                                "VETH_NAMESPACE_IDENTITY",
                                                "VETH_SYSFS_READ_FAILED"})
                self.assertTrue(any(isinstance(item, dict) and item["kind"] == "veth_pending"
                                    for item in created))
                self.assertFalse(any("delete" in args for args in calls))

    def test_apply_uncertain_command_and_replacement_retain_full_dependency_set(self):
        for uncertain in (True, False):
            with self.subTest(command_uncertain=uncertain):
                result, calls, _reads, report, observed_idx, intent, created = (
                    self._apply_through_direct_creation(
                        command_uncertain=uncertain, replace_before_rollback=True))
                self.assertEqual((result, observed_idx), (2, 99))
                self.assertTrue(intent)
                self.assertEqual(report["status"], "ROLLBACK_INCOMPLETE")
                self.assertIn("veth_pending" if uncertain else "veth",
                              report["rollback_unresolved_categories"])
                self.assertIn("namespace", report["rollback_unresolved_categories"])
                self.assertIn("nft_table", report["rollback_unresolved_categories"])
                self.assertIn("grant_marker", report["rollback_unresolved_categories"])
                self.assertIn("file", report["rollback_unresolved_categories"])
                self.assertTrue(any(item[0] == "nft_table" and item[3] is True
                                    for item in created if isinstance(item, tuple)))
                self.assertFalse(any("delete" in args for args in calls))

    def test_pending_rollback_retains_recorded_dependency_without_identity_claim(self):
        item = {"kind": "veth_pending", "namespace_inode": "net:[123]",
                "host_idx": 11, "pilot_idx": 12}
        with mock.patch.object(HOST, "verify_veth_pair",
                               side_effect=AssertionError("rollback must not inspect")) as verify, \
             mock.patch.object(HOST, "nft_expected",
                               side_effect=AssertionError("rollback must not inspect")) as nft, \
             mock.patch.object(HOST, "run",
                               side_effect=AssertionError("rollback must not delete")) as run:
            self.assertEqual(HOST.rollback([item], "192.0.2.1"), ["veth_pending"])
        verify.assert_not_called()
        nft.assert_not_called()
        run.assert_not_called()

    def test_pending_rollback_retains_entire_recorded_dependency_set(self):
        created = [
            {"kind": "namespace_pending", "path_inode": 41},
            {"kind": "nft_table_pending", "namespace": True},
            {"kind": "veth_pending", "namespace_inode": "net:[123]",
             "host_idx": 11, "pilot_idx": 12},
        ]
        with mock.patch.object(HOST, "verify_veth_pair",
                               side_effect=AssertionError("rollback must not inspect")) as verify, \
             mock.patch.object(HOST, "nft_expected",
                               side_effect=AssertionError("rollback must not inspect")) as nft, \
             mock.patch.object(HOST, "run",
                               side_effect=AssertionError("rollback must not delete")) as run:
            self.assertEqual(HOST.rollback(created, "192.0.2.1"),
                             ["veth_pending", "nft_table_pending", "namespace_pending"])
        verify.assert_not_called()
        nft.assert_not_called()
        run.assert_not_called()

    def test_rollback_categories_are_recorded_dependencies_without_presence_claim(self):
        created = [("namespace", "net:[123]", 10), ("nft_table", "inet", HOST.FILTER, True, 41),
                   ("veth", 3, 2, "net:[123]")]
        with mock.patch.object(HOST, "ns_json", side_effect=AssertionError("no live probe")) as links, \
             mock.patch.object(HOST, "veth_sysfs_indexes",
                               side_effect=AssertionError("no live probe")) as sysfs, \
             mock.patch.object(HOST, "run", side_effect=AssertionError("no cleanup")) as run:
            self.assertEqual(HOST.rollback(created, "192.0.2.1"),
                             ["veth", "nft_table", "namespace"])
        links.assert_not_called()
        sysfs.assert_not_called()
        run.assert_not_called()

    def test_completed_rollback_retains_dependency_without_identity_claim(self):
        item = ("veth", 11, 12, "net:[123]")
        with mock.patch.object(HOST, "verify_veth_pair",
                               side_effect=AssertionError("rollback must not inspect")) as verify, \
             mock.patch.object(HOST, "nft_expected",
                               side_effect=AssertionError("rollback must not inspect")) as nft, \
             mock.patch.object(HOST, "run",
                               side_effect=AssertionError("rollback must not delete")) as run:
            self.assertEqual(HOST.rollback([item], "192.0.2.1"), ["veth"])
        verify.assert_not_called()
        nft.assert_not_called()
        run.assert_not_called()



if __name__ == "__main__":
    unittest.main()
