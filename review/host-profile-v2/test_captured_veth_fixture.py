"""Verify the exact raw disposable `ip -j -details link` captures offline."""

import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent
CAPTURE = BASE / "captured-veth"
SPEC = importlib.util.spec_from_file_location("orion_captured_veth", BASE / "apply-host-profile-v2.py")
HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOST)


class CapturedVethFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metadata = json.loads((CAPTURE / "capture_metadata.json").read_text())
        cls.rows = {}
        for name, digest in cls.metadata["file_sha256"].items():
            raw = (CAPTURE / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise AssertionError("CAPTURE_DIGEST_DRIFT")
            cls.rows[name.removesuffix(".json")] = json.loads(raw)

    def test_raw_capture_has_two_disposable_placements_and_exact_peer_evidence(self):
        self.assertEqual(self.metadata["schema"], "orion.veth_ip_link.disposable_raw_capture.v2")
        for key in ("host_network_modified", "uplink_present", "default_route_present",
                    "customer_traffic"):
            self.assertIs(self.metadata[key], False)
        before_host = self.rows["before_host"][0]
        before_peer = self.rows["before_peer"][0]
        after_host = self.rows["after_host"][0]
        after_peer = self.rows["after_peer"][0]
        self.assertEqual((before_host["ifname"], before_peer["ifname"]), (HOST.H, HOST.P))
        self.assertEqual((before_host["link"], before_peer["link"]), (HOST.P, HOST.H))
        self.assertNotIn("link_index", before_host)
        self.assertNotIn("link_index", before_peer)
        self.assertEqual((after_host["ifindex"], after_peer["ifindex"]),
                         (before_host["ifindex"], before_peer["ifindex"]))
        self.assertEqual((after_host["link_index"], after_peer["link_index"]),
                         (before_peer["ifindex"], before_host["ifindex"]))
        self.assertNotIn("link", after_host)
        self.assertNotIn("link", after_peer)
        namespaces = self.rows["sysfs_indexes"]["network_namespaces"]
        self.assertEqual(namespaces["before_host"], namespaces["before_peer"])
        self.assertEqual(namespaces["before_host"], namespaces["after_host"])
        self.assertNotEqual(namespaces["after_host"], namespaces["after_peer"])
        self.assertEqual([row["ifname"] for row in self.rows["before_namespace_listing"]], ["lo"])
        self.assertEqual({row["ifname"] for row in self.rows["after_namespace_listing"]},
                         {"lo", HOST.P})
        for stage in ("before", "after"):
            host = self.rows[f"{stage}_host"][0]
            peer = self.rows[f"{stage}_peer"][0]
            sysfs = self.rows["sysfs_indexes"][stage]
            self.assertEqual(tuple(int(sysfs["host"][key]) for key in ("ifindex", "iflink")),
                             (host["ifindex"], peer["ifindex"]))
            self.assertEqual(tuple(int(sysfs["peer"][key]) for key in ("ifindex", "iflink")),
                             (peer["ifindex"], host["ifindex"]))

    def test_verifier_accepts_both_raw_placements_and_rejects_missing_or_drifted_peer(self):
        host_idx = self.rows["before_host"][0]["ifindex"]
        peer_idx = self.rows["before_peer"][0]["ifindex"]
        for moved in (False, True):
            with self.subTest(moved=moved):
                host = self.rows["after_host" if moved else "before_host"]
                peer = self.rows["after_peer" if moved else "before_peer"]
                self._verify(host, peer, host_idx, peer_idx, moved)

        for label, change in (
            ("missing_both", lambda row: row.pop("link_index")),
            ("wrong_index", lambda row: row.update(link_index=999)),
            ("conflicting_name", lambda row: row.update(link="other")),
            ("malformed_index", lambda row: row.update(link_index=True)),
        ):
            with self.subTest(label=label):
                host = copy.deepcopy(self.rows["after_host"])
                change(host[0])
                with self.assertRaises(HOST.Blocked):
                    self._verify(host, self.rows["after_peer"], host_idx, peer_idx, True)

    def _verify(self, host, peer, host_idx, peer_idx, moved):
        def host_json(args):
            return host if args[-1] == HOST.H else peer

        stage = "after" if moved else "before"
        sysfs = self.rows["sysfs_indexes"][stage]
        indexes = [tuple(int(sysfs[side][key]) for key in ("ifindex", "iflink"))
                   for side in ("host", "peer")]
        with mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command", side_effect=host_json), \
             mock.patch.object(HOST, "ns_json", return_value=peer), \
             mock.patch.object(HOST, "veth_sysfs_indexes", side_effect=indexes):
            HOST.verify_veth_pair(host_idx, peer_idx, "net:[123]", peer_in_namespace=moved)

    def test_sysfs_reader_parses_captured_raw_text(self):
        for stage in ("before", "after"):
            with self.subTest(stage=stage):
                raw = self.rows["sysfs_indexes"][stage]
                with mock.patch.object(HOST.Path, "read_text",
                                       side_effect=[raw["host"][key] for key in ("ifindex", "iflink")]), \
                     mock.patch.object(HOST, "run",
                                       side_effect=[raw["peer"][key] for key in ("ifindex", "iflink")]):
                    self.assertEqual(HOST.veth_sysfs_indexes(HOST.H),
                                     tuple(int(raw["host"][key]) for key in ("ifindex", "iflink")))
                    self.assertEqual(HOST.veth_sysfs_indexes(HOST.P, namespace=True),
                                     tuple(int(raw["peer"][key]) for key in ("ifindex", "iflink")))


if __name__ == "__main__":
    unittest.main()
