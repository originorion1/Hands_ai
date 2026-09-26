"""Offline caller-level veth checks; every host-facing operation is mocked."""

import contextlib
import copy
import importlib.util
import io
import json
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

    def test_create_veth_verifies_pre_move_pair_before_promoting_record(self):
        created = []
        with mock.patch.object(HOST, "run", return_value="") as run, \
             mock.patch.object(HOST, "link_index", side_effect=[11, 12]), \
             mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command",
                               side_effect=[BEFORE["host"], BEFORE["pilot_host"]]), \
             mock.patch.object(HOST, "veth_sysfs_indexes",
                               side_effect=[(11, 12), (12, 11)]) as sysfs:
            self.assertEqual(HOST.create_veth("net:[123]", created), (11, 12))
        self.assertEqual(created, [("veth", 11, 12, "net:[123]")])
        self.assertEqual(run.call_args_list,
                         [mock.call([HOST.IP, "link", "add", HOST.H,
                                     "type", "veth", "peer", "name", HOST.P])])
        self.assertEqual(sysfs.call_args_list,
                         [mock.call(HOST.H), mock.call(HOST.P, namespace=False)])

    def test_create_veth_drift_leaves_pending_record_and_does_not_move(self):
        created = []
        drifted = copy.deepcopy(BEFORE["pilot_host"])
        drifted[0]["link"] = "other"
        with mock.patch.object(HOST, "run", return_value="") as run, \
             mock.patch.object(HOST, "link_index", side_effect=[11, 12]), \
             mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command",
                               side_effect=[BEFORE["host"], drifted]), \
             mock.patch.object(HOST, "veth_sysfs_indexes"), self.assertRaises(HOST.Blocked):
            HOST.create_veth("net:[123]", created)
        self.assertEqual(created, [{"kind": "veth_pending", "namespace_inode": "net:[123]",
                                    "host_idx": 11, "pilot_idx": 12}])
        self.assertEqual(len(run.call_args_list), 1)

    def test_create_veth_uses_raw_capture_and_reciprocal_sysfs_evidence(self):
        host_idx = RAW_BEFORE["host"][0]["ifindex"]
        peer_idx = RAW_BEFORE["pilot_host"][0]["ifindex"]
        sysfs = RAW_SYSFS["before"]
        indexes = [tuple(int(sysfs[side][key]) for key in ("ifindex", "iflink"))
                   for side in ("host", "peer")]
        created = []
        with mock.patch.object(HOST, "run", return_value="") as run, \
             mock.patch.object(HOST, "link_index", side_effect=[host_idx, peer_idx]), \
             mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command",
                               side_effect=[RAW_BEFORE["host"], RAW_BEFORE["pilot_host"]]), \
             mock.patch.object(HOST, "veth_sysfs_indexes", side_effect=indexes) as reads:
            self.assertEqual(HOST.create_veth("net:[123]", created), (host_idx, peer_idx))
        self.assertEqual(created, [("veth", host_idx, peer_idx, "net:[123]")])
        self.assertEqual(reads.call_args_list,
                         [mock.call(HOST.H), mock.call(HOST.P, namespace=False)])
        self.assertEqual(len(run.call_args_list), 1)

    def _apply_through_post_move(self, *, peer_drift=False, pending_failure=False,
                                 replace_after_check=False, use_capture=False):
        """Run mocked apply through veth creation and its recovery path."""
        before_rows = RAW_BEFORE if use_capture else BEFORE
        after_rows = RAW_AFTER if use_capture else AFTER
        host_idx = before_rows["host"][0]["ifindex"]
        peer_idx = before_rows["pilot_host"][0]["ifindex"]
        before = {"routes": {}, "docker": {"nftables": []}, "historical": "x", "units": {}}
        peer = copy.deepcopy(after_rows["pilot_namespace"])
        if peer_drift:
            peer[0]["link"] = "other"
        calls = []
        stderr = io.StringIO()
        original_host = before_rows["host"] if pending_failure else after_rows["host"]
        state = {"host": copy.deepcopy(original_host), "sysfs_reads": 0}

        def created_veth(_inode, created):
            if pending_failure:
                created.append({"kind": "veth_pending", "namespace_inode": "net:[123]",
                                "host_idx": host_idx, "pilot_idx": peer_idx})
                if replace_after_check:
                    state["host"][0]["ifindex"] = 99
                raise HOST.Blocked("STOP_AFTER_PRE_MOVE")
            created.append(("veth", host_idx, peer_idx, "net:[123]"))
            return host_idx, peer_idx

        def current_link(args):
            return before_rows["pilot_host"] if args[-1] == HOST.P else state["host"]

        def checked_sysfs(name, *, namespace=False):
            state["sysfs_reads"] += 1
            if use_capture:
                side = "host" if name == HOST.H else "peer"
                values = RAW_SYSFS["after"][side]
                return tuple(int(values[key]) for key in ("ifindex", "iflink"))
            return (host_idx, peer_idx) if name == HOST.H else (peer_idx, host_idx)

        def fake_run(args, **_kwargs):
            calls.append(args)
            if args[:3] == [HOST.IP, "address", "add"]:
                if replace_after_check:
                    state["host"][0]["ifindex"] = 99
                raise HOST.Blocked("STOP_AFTER_POST_MOVE")
            return ""

        with contextlib.ExitStack() as stack:
            for name, value in (
                ("verify_review", ({}, {})),
                ("policy_tuple", ("192.0.2.1", "fixture.test")),
                ("proposed_units", (b"", b"")),
                ("verify_grant", ({}, "a" * 64, Path("/fixture/used"))),
                ("host_snapshot", before),
                ("absent", True),
                ("create_namespace", "net:[123]"),
                ("route_arrays", {"ipv4_main": []}),
                ("sha", HOST.HOSTS_SHA),
                ("namespace_inode", "net:[123]"),
                ("nft_expected", ([], [], {}, {})),
            ):
                stack.enter_context(mock.patch.object(HOST, name, return_value=value))
            for name in ("one_shot_marker", "ns_ip", "namespace_forwarding_zero",
                         "create_dir", "create_file", "install_nft"):
                stack.enter_context(mock.patch.object(HOST, name))
            ns_rows = ([before_rows["namespace_listing"]] if pending_failure else
                       [before_rows["namespace_listing"], peer,
                        after_rows["namespace_listing"], peer])
            stack.enter_context(mock.patch.object(HOST, "ns_json", side_effect=ns_rows))
            stack.enter_context(mock.patch.object(HOST, "create_veth",
                                                  side_effect=created_veth))
            stack.enter_context(mock.patch.object(HOST, "json_command",
                                                  side_effect=current_link))
            sysfs = stack.enter_context(mock.patch.object(
                HOST, "veth_sysfs_indexes", side_effect=checked_sysfs))
            stack.enter_context(mock.patch.object(HOST, "run", side_effect=fake_run))
            stack.enter_context(mock.patch.object(HOST.os, "open", return_value=7))
            stack.enter_context(mock.patch.object(HOST.os, "close"))
            stack.enter_context(mock.patch.object(HOST.fcntl, "flock"))
            stack.enter_context(mock.patch.object(HOST.signal, "signal"))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            result = HOST.apply()
            sysfs_calls = sysfs.call_args_list
        return result, calls, sysfs_calls, json.loads(stderr.getvalue()), state["host"][0]["ifindex"]

    def test_apply_checks_pair_after_move_before_addressing(self):
        result, calls, sysfs_calls, report, _index = self._apply_through_post_move()
        self.assertEqual(result, 2)  # Deliberate mocked stop at the next action.
        self.assertIn([HOST.IP, "link", "set", HOST.P, "netns", HOST.NS], calls)
        self.assertIn([HOST.IP, "address", "add", HOST.HOST_IP + "/30", "dev", HOST.H], calls)
        self.assertEqual(sysfs_calls,
                         [mock.call(HOST.H), mock.call(HOST.P, namespace=True)])
        self.assertEqual(report["status"], "ROLLBACK_INCOMPLETE")
        self.assertIn("veth", report["rollback_unresolved_categories"])

    def test_apply_post_move_peer_drift_blocks_before_addressing(self):
        result, calls, sysfs_calls, report, _index = self._apply_through_post_move(peer_drift=True)
        self.assertEqual(result, 2)
        self.assertEqual(calls, [[HOST.IP, "link", "set", HOST.P, "netns", HOST.NS]])
        self.assertEqual(sysfs_calls, [])
        self.assertEqual(report["status"], "ROLLBACK_INCOMPLETE")

    def test_apply_replacement_during_rollback_retains_link_and_reports_incomplete(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                result, calls, _sysfs, report, observed_idx = self._apply_through_post_move(
                    pending_failure=pending, replace_after_check=True)
                self.assertEqual(observed_idx, 99)
                self.assertEqual(result, 2)
                self.assertEqual(report["status"], "ROLLBACK_INCOMPLETE")
                self.assertIn("veth_pending" if pending else "veth",
                              report["rollback_unresolved_categories"])
                self.assertNotIn([HOST.IP, "link", "delete", HOST.H], calls)

    def test_raw_capture_post_move_and_pending_rollback_through_apply(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                result, calls, sysfs_calls, report, observed_idx = self._apply_through_post_move(
                    pending_failure=pending, replace_after_check=True, use_capture=True)
                self.assertEqual((result, observed_idx), (2, 99))
                self.assertEqual(report["status"], "ROLLBACK_INCOMPLETE")
                self.assertIn("veth_pending" if pending else "veth",
                              report["rollback_unresolved_categories"])
                self.assertNotIn([HOST.IP, "link", "delete", HOST.H], calls)
                if not pending:
                    self.assertEqual(sysfs_calls,
                                     [mock.call(HOST.H), mock.call(HOST.P, namespace=True)])

    def test_pending_rollback_verifies_pre_move_pair_but_retains_it(self):
        item = {"kind": "veth_pending", "namespace_inode": "net:[123]",
                "host_idx": 11, "pilot_idx": 12}
        with mock.patch.object(HOST, "nft_expected", return_value=([], [], {}, {})), \
             mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command",
                               side_effect=[BEFORE["host"], BEFORE["pilot_host"]]), \
             mock.patch.object(HOST, "veth_sysfs_indexes",
                               side_effect=[(11, 12), (12, 11)]), \
             mock.patch.object(HOST, "run", return_value="") as run:
            self.assertEqual(HOST.rollback([item], "192.0.2.1"), ["veth_pending"])
        run.assert_not_called()

    def test_pending_rollback_missing_sysfs_read_never_deletes(self):
        item = {"kind": "veth_pending", "namespace_inode": "net:[123]",
                "host_idx": 11, "pilot_idx": 12}
        with mock.patch.object(HOST, "nft_expected", return_value=([], [], {}, {})), \
             mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command",
                               side_effect=[BEFORE["host"], BEFORE["pilot_host"]]), \
             mock.patch.object(HOST.Path, "read_text", side_effect=FileNotFoundError), \
             mock.patch.object(HOST, "run") as run:
            self.assertEqual(HOST.rollback([item], "192.0.2.1"), ["veth_pending"])
        run.assert_not_called()

    def test_pending_namespace_is_retained_with_veth_for_manual_recovery(self):
        created = [
            {"kind": "namespace_pending", "path_inode": 41},
            {"kind": "veth_pending", "namespace_inode": "net:[123]",
             "host_idx": 11, "pilot_idx": 12},
        ]
        with mock.patch.object(HOST, "nft_expected", return_value=([], [], {}, {})), \
             mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command",
                               side_effect=[BEFORE["host"], BEFORE["pilot_host"]]), \
             mock.patch.object(HOST, "veth_sysfs_indexes",
                               side_effect=[(11, 12), (12, 11)]), \
             mock.patch.object(HOST, "run") as run:
            self.assertEqual(HOST.rollback(created, "192.0.2.1"),
                             ["veth_pending", "namespace_pending"])
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

    def test_completed_rollback_bad_listing_namespace_or_peer_never_deletes(self):
        item = ("veth", 11, 12, "net:[123]")
        drifted = copy.deepcopy(AFTER["pilot_namespace"])
        drifted[0]["link"] = "other"
        cases = (
            ("listing_not_array", {"ifname": HOST.P}, "net:[123]", AFTER["pilot_namespace"]),
            ("listing_malformed_row", [None], "net:[123]", AFTER["pilot_namespace"]),
            ("listing_ambiguous", [*AFTER["namespace_listing"], drifted[0]],
             "net:[123]", AFTER["pilot_namespace"]),
            ("namespace_changed", AFTER["namespace_listing"], "net:[999]",
             AFTER["pilot_namespace"]),
            ("peer_drift", AFTER["namespace_listing"], "net:[123]", drifted),
        )
        for label, listing, inode, peer in cases:
            with self.subTest(label=label):
                with mock.patch.object(HOST, "nft_expected", return_value=([], [], {}, {})), \
                     mock.patch.object(HOST, "namespace_inode", return_value=inode), \
                     mock.patch.object(HOST, "json_command", return_value=AFTER["host"]), \
                     mock.patch.object(HOST, "ns_json", side_effect=[listing, peer]), \
                     mock.patch.object(HOST, "veth_sysfs_indexes",
                                       side_effect=[(11, 12), (12, 11)]), \
                     mock.patch.object(HOST, "run") as run:
                    self.assertEqual(HOST.rollback([item], "192.0.2.1"), ["veth"])
                run.assert_not_called()

    def test_completed_rollback_missing_namespace_sysfs_read_never_deletes(self):
        item = ("veth", 11, 12, "net:[123]")
        with mock.patch.object(HOST, "nft_expected", return_value=([], [], {}, {})), \
             mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command", return_value=AFTER["host"]), \
             mock.patch.object(HOST, "ns_json",
                               side_effect=[AFTER["namespace_listing"],
                                            AFTER["pilot_namespace"]]), \
             mock.patch.object(HOST.Path, "read_text", side_effect=["11\n", "12\n"]), \
             mock.patch.object(HOST, "run", side_effect=FileNotFoundError) as run:
            self.assertEqual(HOST.rollback([item], "192.0.2.1"), ["veth"])
        self.assertTrue(all(call.args[0][:3] != [HOST.IP, "link", "delete"]
                            for call in run.call_args_list))

    def test_replacement_after_verification_never_issues_delete(self):
        """An external replacement at the check boundary leaves the link retained."""
        for pending in (False, True):
            with self.subTest(pending=pending):
                item = ({"kind": "veth_pending", "namespace_inode": "net:[123]",
                         "host_idx": 11, "pilot_idx": 12} if pending else
                        ("veth", 11, 12, "net:[123]"))
                original = BEFORE if pending else AFTER
                state = {"host": copy.deepcopy(original["host"]), "deleted": []}

                def current_link(args, state=state):
                    if args[-1] == HOST.P:
                        return BEFORE["pilot_host"]
                    return state["host"]

                def fake_run(args, state=state, **_kwargs):
                    if args == [HOST.IP, "link", "delete", HOST.H]:
                        state["deleted"].append(state["host"][0]["ifindex"])
                    return ""

                def checked_sysfs(name, *, namespace=False, original=original, state=state):
                    if name == HOST.P:
                        # The other actor replaces H after the final identity read.
                        replacement = copy.deepcopy(original["host"])
                        replacement[0]["ifindex"] = 99
                        state["host"] = replacement
                        return 12, 11
                    return 11, 12

                ns_rows = ([AFTER["namespace_listing"], AFTER["pilot_namespace"]]
                           if not pending else [])
                with mock.patch.object(HOST, "nft_expected", return_value=([], [], {}, {})), \
                     mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
                     mock.patch.object(HOST, "json_command", side_effect=current_link), \
                     mock.patch.object(HOST, "ns_json", side_effect=ns_rows), \
                     mock.patch.object(HOST, "veth_sysfs_indexes",
                                       side_effect=checked_sysfs), \
                     mock.patch.object(HOST, "run", side_effect=fake_run) as run:
                    self.assertEqual(HOST.rollback([item], "192.0.2.1"),
                                     ["veth_pending" if pending else "veth"])
                self.assertEqual(state["deleted"], [],
                                 "rollback must not delete a replacement of the verified link")
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
