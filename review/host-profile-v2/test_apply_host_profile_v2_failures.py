"""Mock-only failure injection for the guarded v2 host script; no host calls."""

import contextlib
import copy
import datetime as dt
import hashlib
import hmac
import importlib.util
import io
import json
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest import mock

SCRIPT = Path(__file__).with_name("apply-host-profile-v2.py")
SPEC = importlib.util.spec_from_file_location("orion_host_profile_v2_failures", SCRIPT)
HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOST)


class FileFixture:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def fileno(self):
        return 7

    def write(self, _data):
        pass

    def flush(self):
        pass

    def read(self, _limit):
        return b"k" * 32


class BoundaryTests(unittest.TestCase):
    # Reported ip -j -details shape: named peers, no link_index fields.
    HOST_VETH: ClassVar[dict] = {"ifname": HOST.H, "ifindex": 11, "link": HOST.P,
                 "linkinfo": {"info_kind": "veth"}}
    PILOT_VETH: ClassVar[dict] = {"ifname": HOST.P, "ifindex": 12, "link": HOST.H,
                  "linkinfo": {"info_kind": "veth"}}

    def test_review_chain_verifies_disposable_copy_and_rejects_digest_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "review-copy"
            shutil.copytree(HOST.ROOT, root)
            base = root / "host-profile-v2"
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(HOST, "BASE", base))
                stack.enter_context(mock.patch.object(HOST, "ROOT", root))
                for name in ("MANIFEST", "V12_MANIFEST", "V13_MANIFEST", "V14_MANIFEST",
                             "V15_MANIFEST", "V16_MANIFEST", "V17_MANIFEST"):
                    original = getattr(HOST, name)
                    stack.enter_context(mock.patch.object(HOST, name, base / original.name))
                self.assertIsInstance(HOST.verify_review(), tuple)
                fixture = base / "veth_ip_link_representative.json"
                fixture.write_bytes(fixture.read_bytes() + b" ")
                with self.assertRaisesRegex(HOST.Blocked, "V17_FILE_DIGEST"):
                    HOST.verify_review()
                fixture.write_bytes(SCRIPT.with_name(fixture.name).read_bytes())
                archive = base / "apply-host-profile-v2-v16.py"
                archive.write_bytes(archive.read_bytes() + b" ")
                with self.assertRaisesRegex(HOST.Blocked, "V16_REVIEW_BINDING"):
                    HOST.verify_review()

    def test_owner_key_uses_only_root_controller_path(self):
        self.assertEqual(HOST.OWNER_KEY,
                         Path("/etc/orion-pilot/controllers/deployment-approval"))
        self.assertNotIn("/var/lib/orion-pilot/sessions/", str(HOST.OWNER_KEY))
        outer = SimpleNamespace(st_mode=stat.S_IFDIR | 0o750, st_uid=0, st_gid=989)
        inner = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0, st_gid=0)
        key_meta = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0,
                                   st_gid=0, st_nlink=1, st_size=32)
        with mock.patch.object(HOST, "OWNER_CONTROLLERS") as directory, \
             mock.patch.object(HOST.os, "open", return_value=7) as opened, \
             mock.patch.object(HOST.os, "fdopen", return_value=FileFixture()), \
             mock.patch.object(HOST.os, "fstat", return_value=key_meta):
            directory.parent.lstat.return_value = outer
            directory.lstat.return_value = inner
            self.assertEqual(HOST.owner_key_bytes(), b"k" * 32)
            opened.assert_called_once_with(HOST.OWNER_KEY,
                HOST.os.O_RDONLY | HOST.os.O_NOFOLLOW | HOST.os.O_NONBLOCK)

    def test_owner_key_missing_or_wrong_custody_blocks_closed(self):
        outer = SimpleNamespace(st_mode=stat.S_IFDIR | 0o750, st_uid=0, st_gid=989)
        inner = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0, st_gid=0)
        with mock.patch.object(HOST, "OWNER_CONTROLLERS") as directory, \
             mock.patch.object(HOST.os, "open") as opened:
            directory.parent.lstat.return_value = outer
            directory.lstat.return_value = inner
            opened.side_effect = FileNotFoundError
            with self.assertRaises(FileNotFoundError):
                HOST.owner_key_bytes()
            opened.reset_mock()
            directory.parent.lstat.return_value = SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o750, st_uid=997, st_gid=989)
            with self.assertRaises(HOST.Blocked):
                HOST.owner_key_bytes()
            opened.assert_not_called()
            directory.parent.lstat.return_value = outer
            opened.side_effect = None
            opened.return_value = 7
            wrong = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=997,
                                    st_gid=989, st_nlink=1, st_size=32)
            with mock.patch.object(HOST.os, "fdopen", return_value=FileFixture()), \
                 mock.patch.object(HOST.os, "fstat", return_value=wrong), \
                 self.assertRaises(HOST.Blocked):
                HOST.owner_key_bytes()

    def test_file_creation_is_recorded_before_custody_verification(self):
        created = []
        with mock.patch.object(HOST, "absent", return_value=True), \
             mock.patch.object(HOST.os, "open", return_value=7), \
             mock.patch.object(HOST.os, "fdopen", return_value=FileFixture()), \
             mock.patch.object(HOST.os, "fstat", return_value=SimpleNamespace(st_ino=41)), \
             mock.patch.object(HOST.os, "fchown"), mock.patch.object(HOST.os, "fchmod"), \
             mock.patch.object(HOST.os, "fsync"), \
             mock.patch.object(HOST, "strict_file", side_effect=HOST.Blocked("INJECTED")), \
             self.assertRaises(HOST.Blocked):
            HOST.create_file(Path("/fixture/new"), b"value", 0, 0, 0o600, created)
        self.assertEqual(created, [{"kind": "file_pending", "path": Path("/fixture/new"), "inode": 41}])

    def test_one_use_marker_is_recorded_before_verification_and_retained(self):
        created = []
        with mock.patch.object(HOST.os, "open", return_value=7), \
             mock.patch.object(HOST.os, "fdopen", return_value=FileFixture()), \
             mock.patch.object(HOST.os, "fstat", return_value=SimpleNamespace(st_ino=43)), \
             mock.patch.object(HOST.os, "fchown"), mock.patch.object(HOST.os, "fchmod"), \
             mock.patch.object(HOST.os, "fsync"), \
             mock.patch.object(HOST, "strict_file", side_effect=HOST.Blocked("INJECTED")), \
             self.assertRaises(HOST.Blocked):
            HOST.one_shot_marker(Path("/fixture/used"), "a" * 64, created)
        self.assertEqual(created[0]["kind"], "grant_marker_pending")
        self.assertEqual(created[0]["inode"], 43)

    def test_directory_creation_is_recorded_before_custody_verification(self):
        created = []
        path = mock.Mock()
        path.lstat.return_value = SimpleNamespace(st_ino=42, st_mode=stat.S_IFDIR | 0o700,
                                                   st_uid=9, st_gid=9)
        with mock.patch.object(HOST, "absent", return_value=True), \
             mock.patch.object(HOST.os, "mkdir"), mock.patch.object(HOST.os, "chown"), \
             mock.patch.object(HOST.os, "chmod"), self.assertRaises(HOST.Blocked):
            HOST.create_dir(path, 0, 0, 0o700, created)
        self.assertEqual(created, [{"kind": "dir_pending", "path": path, "inode": 42}])

    def test_namespace_creation_is_recorded_before_inode_read(self):
        created = []
        with mock.patch.object(HOST, "run", return_value=""), \
             mock.patch.object(HOST, "NS_PATH") as ns_path, \
             mock.patch.object(HOST, "namespace_inode", side_effect=HOST.Blocked("INJECTED")), \
             self.assertRaises(HOST.Blocked):
            ns_path.lstat.return_value = SimpleNamespace(st_ino=44)
            HOST.create_namespace(created)
        self.assertEqual(created, [{"kind": "namespace_pending", "path_inode": 44}])

    def test_veth_creation_is_recorded_before_index_read(self):
        created = []
        with mock.patch.object(HOST, "run", return_value=""), \
             mock.patch.object(HOST, "link_index", side_effect=HOST.Blocked("INJECTED")), \
             self.assertRaises(HOST.Blocked):
            HOST.create_veth("net:[123]", created)
        self.assertEqual(created, [{"kind": "veth_pending", "namespace_inode": "net:[123]",
                                    "host_idx": None, "pilot_idx": None}])

    def test_nft_table_creation_is_recorded_before_handle_read(self):
        created = []
        with mock.patch.object(HOST, "nft_run"), \
             mock.patch.object(HOST, "nft_json", side_effect=HOST.Blocked("INJECTED")), \
             self.assertRaises(HOST.Blocked):
            HOST.add_table("ip", HOST.NAT, namespace=False, created=created)
        self.assertEqual(created, [{"kind": "nft_table_pending", "family": "ip",
                                    "name": HOST.NAT, "namespace": False, "handle": None}])

    def test_docker_rule_creation_is_recorded_before_handle_read(self):
        created = []
        with mock.patch.object(HOST, "nft_run"), \
             mock.patch.object(HOST, "nft_json", side_effect=[{"nftables": []}, HOST.Blocked("INJECTED")]), \
             self.assertRaises(HOST.Blocked):
            HOST.add_docker_rule(("comment", HOST.COMMENT), {"rule": {}}, created)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["kind"], "docker_rule_pending")

    def test_unverified_pending_resource_never_reports_complete_rollback(self):
        with mock.patch.object(HOST, "nft_expected", return_value=([], [], {}, {})):
            self.assertEqual(HOST.rollback([{"kind": "file_pending", "path": Path("/fixture/new"),
                                             "inode": None}], "192.0.2.1"), ["file_pending"])

    def test_failed_cleanup_audit_reports_rollback_incomplete(self):
        preflight = {"routes": {}, "docker": {"nftables": []},
                     "historical": "a" * 64, "units": {}}
        stderr = io.StringIO()
        with mock.patch.object(HOST, "verify_review", return_value=({}, {})), \
             mock.patch.object(HOST, "policy_tuple", return_value=("192.0.2.1", "fixture.test")), \
             mock.patch.object(HOST, "proposed_units", return_value=(b"", b"")), \
             mock.patch.object(HOST, "verify_grant", return_value=({}, "b" * 64, Path("/unused"))), \
             mock.patch.object(HOST, "host_snapshot", return_value=preflight), \
             mock.patch.object(HOST, "absent", return_value=False), \
             mock.patch.object(HOST, "one_shot_marker"), \
             mock.patch.object(HOST, "create_namespace", side_effect=HOST.Blocked("INJECTED")), \
             mock.patch.object(HOST, "rollback", return_value=[]), \
             mock.patch.object(HOST, "rollback_proven", side_effect=HOST.Blocked("UNPROVEN")), \
             mock.patch.object(HOST.os, "open", return_value=7), \
             mock.patch.object(HOST.os, "close"), \
             mock.patch.object(HOST.fcntl, "flock"), \
             mock.patch.object(HOST.signal, "signal"), \
             contextlib.redirect_stderr(stderr), \
             mock.patch.object(HOST, "absent", side_effect=[True, False, False]):
            # First absent() call is the grant-marker gate; subsequent calls
            # must report the marker present for the cleanup audit.
            self.assertEqual(HOST.apply(), 2)
        self.assertEqual(json.loads(stderr.getvalue())["status"], "ROLLBACK_INCOMPLETE")

    def test_veth_accepts_reported_named_peer_shape_with_sysfs_indexes(self):
        host, peer = copy.deepcopy(self.HOST_VETH), copy.deepcopy(self.PILOT_VETH)
        with mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command", return_value=[host]), \
             mock.patch.object(HOST, "ns_json", return_value=[peer]), \
             mock.patch.object(HOST, "veth_sysfs_indexes",
                               side_effect=[(11, 12), (12, 11)]) as sysfs:
            HOST.verify_veth_pair(11, 12, "net:[123]")
            self.assertEqual(sysfs.call_args_list,
                             [mock.call(HOST.H), mock.call(HOST.P, namespace=True)])

    def test_veth_rejects_malformed_or_inconsistent_peer_evidence(self):
        cases = (
            ("missing_host_peer", {"link": None}, {}, (11, 12), (12, 11)),
            ("wrong_host_peer", {"link": "other"}, {}, (11, 12), (12, 11)),
            ("missing_pilot_peer", {}, {"link": None}, (11, 12), (12, 11)),
            ("wrong_pilot_peer", {}, {"link": "other"}, (11, 12), (12, 11)),
            ("wrong_host_index", {"ifindex": 99}, {}, (11, 12), (12, 11)),
            ("wrong_pilot_index", {}, {"ifindex": 99}, (11, 12), (12, 11)),
            ("wrong_optional_link_index", {"link_index": 99}, {}, (11, 12), (12, 11)),
            ("ambiguous_optional_link_index", {}, {"link_index": True}, (11, 12), (12, 11)),
            ("wrong_kind", {}, {"linkinfo": {"info_kind": "dummy"}}, (11, 12), (12, 11)),
            ("wrong_host_iflink", {}, {}, (11, 99), (12, 11)),
            ("wrong_pilot_iflink", {}, {}, (11, 12), (12, 99)),
            ("wrong_pilot_sysfs_index", {}, {}, (11, 12), (99, 11)),
        )
        for label, host_change, peer_change, host_sysfs, peer_sysfs in cases:
            with self.subTest(label=label):
                host, peer = copy.deepcopy(self.HOST_VETH), copy.deepcopy(self.PILOT_VETH)
                host.update(host_change)
                peer.update(peer_change)
                with mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
                     mock.patch.object(HOST, "json_command", return_value=[host]), \
                     mock.patch.object(HOST, "ns_json", return_value=[peer]), \
                     mock.patch.object(HOST, "veth_sysfs_indexes",
                                       side_effect=[host_sysfs, peer_sysfs]), \
                     self.assertRaises(HOST.Blocked):
                    HOST.verify_veth_pair(11, 12, "net:[123]")

    def test_veth_rejects_ambiguous_peer_rows_and_namespace_change(self):
        host, peer = copy.deepcopy(self.HOST_VETH), copy.deepcopy(self.PILOT_VETH)
        with mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command", return_value=[host]), \
             mock.patch.object(HOST, "ns_json", return_value=[peer, peer]), \
             mock.patch.object(HOST, "veth_sysfs_indexes") as sysfs:
            with self.assertRaises(HOST.Blocked):
                HOST.verify_veth_pair(11, 12, "net:[123]")
            sysfs.assert_not_called()
        with mock.patch.object(HOST, "namespace_inode", return_value="net:[999]"), \
             mock.patch.object(HOST, "json_command") as links:
            with self.assertRaises(HOST.Blocked):
                HOST.verify_veth_pair(11, 12, "net:[123]")
            links.assert_not_called()

    def test_veth_sysfs_reads_both_indexes_in_endpoint_namespace(self):
        with mock.patch.object(HOST.Path, "read_text", side_effect=["11\n", "12\n"]):
            self.assertEqual(HOST.veth_sysfs_indexes(HOST.H), (11, 12))
        with mock.patch.object(HOST, "run", side_effect=["12\n", "11\n"]) as run:
            self.assertEqual(HOST.veth_sysfs_indexes(HOST.P, namespace=True), (12, 11))
            self.assertEqual(run.call_args_list, [
                mock.call([HOST.IP, "netns", "exec", HOST.NS, "/usr/bin/cat",
                           "/sys/class/net/" + HOST.P + "/ifindex"]),
                mock.call([HOST.IP, "netns", "exec", HOST.NS, "/usr/bin/cat",
                           "/sys/class/net/" + HOST.P + "/iflink"]),
            ])
        with mock.patch.object(HOST.Path, "read_text", return_value="unknown\n"), \
             self.assertRaises(HOST.Blocked):
            HOST.veth_sysfs_indexes(HOST.H)

    def test_rollback_refuses_veth_with_wrong_peer_index(self):
        host = copy.deepcopy(self.HOST_VETH)
        wrong_peer = copy.deepcopy(self.PILOT_VETH)
        wrong_peer["link_index"] = 99
        with mock.patch.object(HOST, "nft_expected", return_value=([], [], {}, {})), \
             mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command", return_value=[host]), \
             mock.patch.object(HOST, "ns_json", side_effect=[[wrong_peer], [wrong_peer]]), \
             mock.patch.object(HOST, "veth_sysfs_indexes") as sysfs, \
             mock.patch.object(HOST, "run") as run:
            self.assertEqual(HOST.rollback([("veth", 11, 12, "net:[123]")], "192.0.2.1"),
                             ["veth"])
        sysfs.assert_not_called()
        run.assert_not_called()

    def test_rollback_refuses_wrong_sysfs_peer_without_deletion(self):
        with mock.patch.object(HOST, "nft_expected", return_value=([], [], {}, {})), \
             mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command", return_value=[self.HOST_VETH]), \
             mock.patch.object(HOST, "ns_json", side_effect=[[self.PILOT_VETH],
                                                            [self.PILOT_VETH]]), \
             mock.patch.object(HOST, "veth_sysfs_indexes", side_effect=[(11, 12), (12, 99)]), \
             mock.patch.object(HOST, "run") as run:
            self.assertEqual(HOST.rollback([("veth", 11, 12, "net:[123]")], "192.0.2.1"),
                             ["veth"])
        run.assert_not_called()

    def test_rollback_retains_verified_pair_in_either_placement(self):
        for in_namespace in (True, False):
            with self.subTest(in_namespace=in_namespace):
                listing = [self.PILOT_VETH] if in_namespace else []
                with mock.patch.object(HOST, "nft_expected", return_value=([], [], {}, {})), \
                     mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
                     mock.patch.object(HOST, "json_command", return_value=[self.HOST_VETH]), \
                     mock.patch.object(HOST, "ns_json", return_value=listing), \
                     mock.patch.object(HOST, "veth_sysfs_indexes",
                                       side_effect=[(11, 12), (12, 11)]) as sysfs, \
                     mock.patch.object(HOST, "run", return_value="") as run:
                    if not in_namespace:
                        # The pre-move peer is queried through json_command.
                        with mock.patch.object(HOST, "json_command",
                                               side_effect=[[self.HOST_VETH], [self.PILOT_VETH]]):
                            failures = HOST.rollback([("veth", 11, 12, "net:[123]")],
                                                     "192.0.2.1")
                    else:
                        failures = HOST.rollback([("veth", 11, 12, "net:[123]")],
                                                 "192.0.2.1")
                    self.assertEqual(failures, ["veth"])
                    sysfs.assert_not_called()
                    run.assert_not_called()

    def test_rollback_refuses_ambiguous_namespace_peer_listing(self):
        with mock.patch.object(HOST, "nft_expected", return_value=([], [], {}, {})), \
             mock.patch.object(HOST, "ns_json",
                               return_value=[self.PILOT_VETH, self.PILOT_VETH]), \
             mock.patch.object(HOST, "json_command") as links, \
             mock.patch.object(HOST, "run") as run:
            self.assertEqual(HOST.rollback([("veth", 11, 12, "net:[123]")], "192.0.2.1"),
                             ["veth"])
        links.assert_not_called()
        run.assert_not_called()

    def test_owner_approval_is_authenticated_and_exact_subject_bound(self):
        now = dt.datetime.now(dt.UTC)
        expiry = now + dt.timedelta(minutes=20)
        subject = {
            "schema": "orion.host_profile_v2.host_application_grant.v1",
            "status": "OWNER_AUTHORIZED_HOST_APPLICATION", "scope": "host_application_only",
            "session_id": HOST.SESSION, "manifest_v9_sha256": HOST.MANIFEST_SHA,
            "manifest_v11_sha256": HOST.V11_SHA,
            "manifest_v12_sha256": HOST.sha(HOST.V12_MANIFEST),
            "manifest_v15_sha256": HOST.sha(HOST.V15_MANIFEST),
            "manifest_v16_sha256": HOST.sha(HOST.V16_MANIFEST),
            "manifest_v17_sha256": HOST.sha(HOST.V17_MANIFEST),
            "profile_sha256": HOST.PROFILE_SHA,
            "script_sha256": HOST.sha(SCRIPT), "grant_id": "b" * 32,
            "expires_at_utc": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "owner_declaration": "I authorize one stopped, unqualified host-profile v2 application for "
                                 + HOST.SESSION + " bound to the stated manifest and script digests. "
                                 "No DNS/TLS or customer activity is authorized.",
        }
        payload = {"version": HOST.APPROVAL_VERSION, "purpose": HOST.APPROVAL_PURPOSE,
                   "subject_sha256": hashlib.sha256(json.dumps(subject, sort_keys=True,
                         separators=(",", ":")).encode()).hexdigest(),
                   "controller_id": "fixture-owner", "issued_at": (now-dt.timedelta(minutes=1)).isoformat(),
                   "expires_at": (now+dt.timedelta(minutes=10)).isoformat()}
        with tempfile.TemporaryDirectory() as temporary:
            test_key = Path(temporary) / "disposable-test-key"
            test_key.write_bytes(b"disposable-test-key-only" * 2)
            key = test_key.read_bytes()
            mac = hmac.new(key, json.dumps((HOST.BROKER_VERSION, HOST.APPROVAL_PURPOSE, payload),
                           sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
            record = {**subject, "approval": {**payload, "mac": mac}}
            grant = mock.Mock()
            grant.parent.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | 0o750,
                                                              st_uid=0, st_gid=989)
            raw_grant = lambda: json.dumps(record).encode()
            with mock.patch.object(HOST, "GRANT", grant), \
                 mock.patch.object(HOST.os, "geteuid", return_value=0), \
                 mock.patch.object(HOST, "grant_bytes", side_effect=raw_grant), \
                 mock.patch.object(HOST, "owner_key_bytes", side_effect=test_key.read_bytes), \
                 mock.patch.object(HOST, "absent", return_value=True):
                self.assertEqual(HOST.verify_grant()[1], hashlib.sha256(raw_grant()).hexdigest())
                record["approval"]["mac"] = "0" * 64
                with self.assertRaises(HOST.Blocked):
                    HOST.verify_grant()
                record["approval"]["mac"] = mac
                record["profile_sha256"] = "0" * 64
                with self.assertRaises(HOST.Blocked):
                    HOST.verify_grant()
                record["profile_sha256"] = HOST.PROFILE_SHA
                record["manifest_v17_sha256"] = "0" * 64
                with self.assertRaisesRegex(HOST.Blocked, "GRANT_BINDING"):
                    HOST.verify_grant()
                record["manifest_v17_sha256"] = subject["manifest_v17_sha256"]
                with mock.patch.object(HOST, "owner_key_bytes", side_effect=FileNotFoundError), \
                     self.assertRaises(FileNotFoundError):
                    HOST.verify_grant()


if __name__ == "__main__":
    unittest.main()
