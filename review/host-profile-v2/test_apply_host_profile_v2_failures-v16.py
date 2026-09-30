"""Mock-only failure injection for the guarded v2 host script; no host calls."""

import contextlib
import datetime as dt
import hashlib
import hmac
import importlib.util
import io
import json
import stat
from pathlib import Path
from types import SimpleNamespace
import unittest
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
                 mock.patch.object(HOST.os, "fstat", return_value=wrong):
                with self.assertRaises(HOST.Blocked):
                    HOST.owner_key_bytes()

    def test_file_creation_is_recorded_before_custody_verification(self):
        created = []
        with mock.patch.object(HOST, "absent", return_value=True), \
             mock.patch.object(HOST.os, "open", return_value=7), \
             mock.patch.object(HOST.os, "fdopen", return_value=FileFixture()), \
             mock.patch.object(HOST.os, "fstat", return_value=SimpleNamespace(st_ino=41)), \
             mock.patch.object(HOST.os, "fchown"), mock.patch.object(HOST.os, "fchmod"), \
             mock.patch.object(HOST.os, "fsync"), \
             mock.patch.object(HOST, "strict_file", side_effect=HOST.Blocked("INJECTED")):
            with self.assertRaises(HOST.Blocked):
                HOST.create_file(Path("/fixture/new"), b"value", 0, 0, 0o600, created)
        self.assertEqual(created, [{"kind": "file_pending", "path": Path("/fixture/new"), "inode": 41}])

    def test_one_use_marker_is_recorded_before_verification_and_retained(self):
        created = []
        with mock.patch.object(HOST.os, "open", return_value=7), \
             mock.patch.object(HOST.os, "fdopen", return_value=FileFixture()), \
             mock.patch.object(HOST.os, "fstat", return_value=SimpleNamespace(st_ino=43)), \
             mock.patch.object(HOST.os, "fchown"), mock.patch.object(HOST.os, "fchmod"), \
             mock.patch.object(HOST.os, "fsync"), \
             mock.patch.object(HOST, "strict_file", side_effect=HOST.Blocked("INJECTED")):
            with self.assertRaises(HOST.Blocked):
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
             mock.patch.object(HOST.os, "chmod"):
            with self.assertRaises(HOST.Blocked):
                HOST.create_dir(path, 0, 0, 0o700, created)
        self.assertEqual(created, [{"kind": "dir_pending", "path": path, "inode": 42}])

    def test_namespace_creation_is_recorded_before_inode_read(self):
        created = []
        with mock.patch.object(HOST, "run", return_value=""), \
             mock.patch.object(HOST, "NS_PATH") as ns_path, \
             mock.patch.object(HOST, "namespace_inode", side_effect=HOST.Blocked("INJECTED")):
            ns_path.lstat.return_value = SimpleNamespace(st_ino=44)
            with self.assertRaises(HOST.Blocked):
                HOST.create_namespace(created)
        self.assertEqual(created, [{"kind": "namespace_pending", "path_inode": 44}])

    def test_veth_creation_is_recorded_before_index_read(self):
        created = []
        with mock.patch.object(HOST, "run", return_value=""), \
             mock.patch.object(HOST, "link_index", side_effect=HOST.Blocked("INJECTED")):
            with self.assertRaises(HOST.Blocked):
                HOST.create_veth("net:[123]", created)
        self.assertEqual(created, [{"kind": "veth_pending", "namespace_inode": "net:[123]",
                                    "host_idx": None, "pilot_idx": None}])

    def test_nft_table_creation_is_recorded_before_handle_read(self):
        created = []
        with mock.patch.object(HOST, "nft_run"), \
             mock.patch.object(HOST, "nft_json", side_effect=HOST.Blocked("INJECTED")):
            with self.assertRaises(HOST.Blocked):
                HOST.add_table("ip", HOST.NAT, namespace=False, created=created)
        self.assertEqual(created, [{"kind": "nft_table_pending", "family": "ip",
                                    "name": HOST.NAT, "namespace": False, "handle": None}])

    def test_docker_rule_creation_is_recorded_before_handle_read(self):
        created = []
        with mock.patch.object(HOST, "nft_run"), \
             mock.patch.object(HOST, "nft_json", side_effect=[{"nftables": []}, HOST.Blocked("INJECTED")]):
            with self.assertRaises(HOST.Blocked):
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
             contextlib.redirect_stderr(stderr):
            # First absent() call is the grant-marker gate; subsequent calls
            # must report the marker present for the cleanup audit.
            with mock.patch.object(HOST, "absent", side_effect=[True, False, False]):
                self.assertEqual(HOST.apply(), 2)
        self.assertEqual(json.loads(stderr.getvalue())["status"], "ROLLBACK_INCOMPLETE")

    def test_veth_requires_reciprocal_indexes_and_namespace_inode(self):
        host = {"ifname": HOST.H, "ifindex": 11, "link_index": 12,
                "linkinfo": {"info_kind": "veth"}}
        peer = {"ifname": HOST.P, "ifindex": 12, "link_index": 11,
                "linkinfo": {"info_kind": "veth"}}
        with mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command", return_value=[host]), \
             mock.patch.object(HOST, "ns_json", return_value=[peer]):
            HOST.verify_veth_pair(11, 12, "net:[123]")
            peer["link_index"] = 99
            with self.assertRaises(HOST.Blocked):
                HOST.verify_veth_pair(11, 12, "net:[123]")
            peer["link_index"] = 11
            with self.assertRaises(HOST.Blocked):
                HOST.verify_veth_pair(11, 12, "net:[999]")

    def test_rollback_refuses_veth_with_wrong_peer_index(self):
        host = {"ifname": HOST.H, "ifindex": 11, "link_index": 12,
                "linkinfo": {"info_kind": "veth"}}
        wrong_peer = {"ifname": HOST.P, "ifindex": 12, "link_index": 99,
                      "linkinfo": {"info_kind": "veth"}}
        with mock.patch.object(HOST, "nft_expected", return_value=([], [], {}, {})), \
             mock.patch.object(HOST, "namespace_inode", return_value="net:[123]"), \
             mock.patch.object(HOST, "json_command", side_effect=[[host], [wrong_peer]]), \
             mock.patch.object(HOST, "ns_json", return_value=[wrong_peer]), \
             mock.patch.object(HOST, "run") as run:
            self.assertEqual(HOST.rollback([("veth", 11, 12, "net:[123]")], "192.0.2.1"),
                             ["veth"])
        run.assert_not_called()

    def test_owner_approval_is_authenticated_and_exact_subject_bound(self):
        now = dt.datetime.now(dt.timezone.utc)
        expiry = now + dt.timedelta(minutes=20)
        subject = {
            "schema": "orion.host_profile_v2.host_application_grant.v1",
            "status": "OWNER_AUTHORIZED_HOST_APPLICATION", "scope": "host_application_only",
            "session_id": HOST.SESSION, "manifest_v9_sha256": HOST.MANIFEST_SHA,
            "manifest_v11_sha256": HOST.V11_SHA, "manifest_v12_sha256": "a" * 64,
            "manifest_v15_sha256": "a" * 64,
            "manifest_v16_sha256": "a" * 64,
            "profile_sha256": HOST.PROFILE_SHA,
            "script_sha256": "a" * 64, "grant_id": "b" * 32,
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
        key = b"k" * 32
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
             mock.patch.object(HOST, "owner_key_bytes", return_value=key), \
             mock.patch.object(HOST, "sha", return_value="a" * 64), \
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
            with mock.patch.object(HOST, "owner_key_bytes", side_effect=FileNotFoundError):
                with self.assertRaises(FileNotFoundError):
                    HOST.verify_grant()


if __name__ == "__main__":
    unittest.main()
