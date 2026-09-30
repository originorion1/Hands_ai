#!/usr/bin/env python3
"""One-shot ORION v2 host staging. REVIEW ONLY until separately owner-authorized.

--check reads host state; --apply requires a future root-owned, script-bound grant.
Neither mode performs DNS/TLS or customer HTTP. Never enable or start units.
"""

from __future__ import annotations

import argparse
from collections import Counter
import ctypes
import datetime as dt
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
MANIFEST = BASE / "REVIEW_MANIFEST_20260926_9.json"
MANIFEST_SHA = "e099ca792a7dfcbbc34c4bc761bb6c90acef600392223e08df44fae3e63d721f"
V11_SHA = "661d7986f0cdf558921a226996f2042385458ec7c7108fb00744b7d1d2cb0f8d"
V12_MANIFEST = BASE / "REVIEW_MANIFEST_20260926_12.json"
V13_MANIFEST = BASE / "REVIEW_MANIFEST_20260926_13.json"
V14_MANIFEST = BASE / "REVIEW_MANIFEST_20260926_14.json"
V15_MANIFEST = BASE / "REVIEW_MANIFEST_20260926_15.json"
PROFILE_SHA = "a11b2cc5ae6612e2e0d84d340b1ffe28d527a23ad859ceeed2dea6965ae7fc43"
SCHEMA_SHA = "14228ce1dd432212fa432360443ab95c656e60662ffd546d04bbeff1f06b2f27"
DECISION_SHA = "50c4cb3b420eaa8beca5794fe64457f3b342457e4744329608ce4dfb980b64b0"
ACCEPTANCE_SHA = "568be3dd48ca7d1dc706ad27f4a2afa3fa7d3f639b55ae21014baf8eff87ca85"
V7_SHA = "15ccc0c0d9afb94756c2fe6720321da4b37b121f4e45dad292a7d09ef6316241"
V8_SHA = "a147e92aeecb7399f7f51d9ddab74c30c41fd07b0b536f7071d8632f2aa4daa7"
SYNTHETIC_SHA = "dc855d17b5f22f0d84b9895636a4e68bb100607b70f6794eb96a909d816b55d3"
PROCEDURE_SHA = "32089d367b133226d6b971d173fda2383a7e595cff4a4e0a62f1ac0224c79399"
NETWORK_SHA = "e4b9104a9c3f43a91c0e925a028e30f4afc8471710e363f9c8130041637daf8d"
TUPLE_SHA = "27d89f516277ffe89ccf66636622125eb680e412b0c6f733301a16df29431878"
HOSTS_SHA = "437f03b01c85e95cc39d54d34fdf17e6cde796fe41b58acedf8a4dba445af736"
OLD_SOCKET_SHA = "76d1caf6f34902813812e40b2ae6af1abce84b7f528f3000f7a01280193b1fce"
OLD_TEMPLATE_SHA = "5ff6ae2cdf512b295dd0ee5bfcc1be6e7ff343b17cd1988d073a0ba82fe5a789"
NEW_SOCKET_SHA = "a106b09e4ec78418e2914c7f6a5af7b6095a486af1d72818a0f0580827888fac"
NEW_TEMPLATE_SHA = "1dd429645f160cd55038366f5a971c8239f619d906dcf2188069a653572b67cf"
INTERPRETER_SHA = "e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f"
SESSION = "orion-metadata-fc4cb43c1468d83a558b589a"
NS = "orion-pilot-fc4cb43c-v2"
H = "orionp0"
P = "orionp1"
NETWORK = ipaddress.ip_network("10.254.205.0/30")
HOST_IP = "10.254.205.1"
PILOT_IP = "10.254.205.2"
NAT = "orion_pilot_nat_v2_fc4cb43c"
FILTER = "orion_pilot_v2_fc4cb43c"
COMMENT = NS
OLD_SOCKET = "orion-pilot-metadata.socket"
OLD_TEMPLATE = "orion-pilot-metadata@.service"
NEW_SOCKET = "orion-pilot-metadata-v2.socket"
NEW_TEMPLATE = "orion-pilot-metadata-v2@.service"
SOCKET_PATH = Path("/run/orion-pilot-v2/operator.sock")
NS_PATH = Path("/run/netns") / NS
RESOLVER_DIR = Path("/etc/netns") / NS
RESOLVER = RESOLVER_DIR / "hosts"
MARKER = ROOT / "host-base-namespace-v2.inode"
NETWORK_MARKER = Path("/etc/orion-pilot/network-provisioned-v2")
UNITS = Path("/etc/systemd/system")
RECEIPTS = Path("/var/lib/orion-pilot/host-profile-v2/receipts")
GRANT = Path("/etc/orion-pilot/HOST_APPLICATION_OWNER_GRANT_V2.json")
OWNER_CONTROLLERS = Path("/etc/orion-pilot/controllers")
OWNER_KEY = OWNER_CONTROLLERS / "deployment-approval"
APPROVAL_PURPOSE = "host_profile_v2_application"
APPROVAL_VERSION = "orion-trusted-controller-approval-v1"
BROKER_VERSION = "local-broker-v3"
IP = "/usr/sbin/ip"
NFT = "/usr/sbin/nft"
SYSTEMCTL = "/usr/bin/systemctl"
ROUTE_KEYS = ("ipv4_main", "ipv4_all", "ipv6_main", "ipv6_all")
SYSTEMCTL_CHECK_LABELS = frozenset({
    "SHOW_OLD_SOCKET", "SHOW_V2_SOCKET", "SHOW_PLAIN_SERVICE",
    "LIST_OLD_INSTANCE_FILES", "LIST_OLD_INSTANCES", "SHOW_OLD_INSTANCE",
    "LIST_V2_INSTANCE_FILES", "LIST_V2_INSTANCES", "SHOW_V2_INSTANCE",
    "VERIFY_V2_UNIT_FILES",
})


class Blocked(Exception):
    pass


class PublishedReceiptError(Blocked):
    def __init__(self, path, digest):
        super().__init__("RECEIPT_POST_PUBLISH_VERIFY")
        self.path = path
        self.digest = digest


def require(ok: bool, code: str) -> None:
    if not ok:
        raise Blocked(code)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def parse_json(raw: str):
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(Blocked("INVALID_JSON_NUMBER")))


def canonical(value) -> bytes:
    def domain(item):
        if item is None or isinstance(item, (str, bool)):
            return
        if type(item) is int:
            require(-(2**63) <= item < 2**63, "JSON_INTEGER_RANGE")
            return
        if isinstance(item, list):
            for child in item:
                domain(child)
            return
        if isinstance(item, dict):
            require(all(isinstance(key, str) for key in item), "JSON_KEY_TYPE")
            for child in item.values():
                domain(child)
            return
        raise Blocked("JSON_DOMAIN")
    domain(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def multiset(rows):
    require(type(rows) is list and all(type(row) is dict for row in rows), "ROUTE_SHAPE")
    return Counter(canonical(row) for row in rows)


def multiset_hash(rows) -> str:
    return sha_bytes(b"[" + b",".join(sorted(canonical(row) for row in rows)) + b"]")


def run(args: list[str], *, input_text: str | None = None, timeout: int = 15,
        systemctl_check_label: str | None = None) -> str:
    if systemctl_check_label is not None:
        require(args[0] == SYSTEMCTL and systemctl_check_label in SYSTEMCTL_CHECK_LABELS,
                "SYSTEMCTL_DIAGNOSTIC_LABEL")
    result = subprocess.run(args, input=input_text, text=True, capture_output=True,
                            timeout=timeout, check=False)
    if result.returncode and systemctl_check_label is not None:
        raise Blocked(f"SYSTEMCTL_{systemctl_check_label}_RC_{result.returncode}")
    require(result.returncode == 0, "COMMAND_FAILED_" + Path(args[0]).name.upper().replace("-", "_"))
    return result.stdout


def json_command(args: list[str]):
    return parse_json(run(args))


def ns_ip(*args):
    return run([IP, "-n", NS, *args])


def ns_json(*args):
    return json_command([IP, "-n", NS, "-j", *args])


def nft_json(*args, namespace=False):
    prefix = [IP, "netns", "exec", NS] if namespace else []
    return json_command([*prefix, NFT, "-j", *args])


def nft_run(*args, namespace=False):
    prefix = [IP, "netns", "exec", NS] if namespace else []
    return run([*prefix, NFT, *args])


def route_arrays(namespace=False):
    result = {}
    for family in ("4", "6"):
        for table in ("main", "all"):
            key = f"ipv{family}_{table}"
            argv = [IP]
            if namespace:
                argv += ["-n", NS]
            result[key] = json_command([*argv, "-j", "-details", "-" + family,
                                         "route", "show", "table", table])
            require(type(result[key]) is list, "ROUTE_JSON_SHAPE")
    return result


def strict_file(path: Path, uid: int, gid: int, mode: int, expected_sha: str | None = None):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "FILE_CUSTODY")
    require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (uid, gid, mode), "FILE_CUSTODY")
    if expected_sha:
        require(sha(path) == expected_sha, "FILE_DIGEST")
    return info


def absent(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def owner_key_bytes():
    """Read only the root-owned controller key from one verified fd."""
    outer, inner = OWNER_CONTROLLERS.parent.lstat(), OWNER_CONTROLLERS.lstat()
    require(stat.S_ISDIR(outer.st_mode) and
            (outer.st_uid, outer.st_gid, stat.S_IMODE(outer.st_mode)) == (0, 989, 0o750) and
            stat.S_ISDIR(inner.st_mode) and
            (inner.st_uid, inner.st_gid, stat.S_IMODE(inner.st_mode)) == (0, 0, 0o700),
            "OWNER_KEY_PARENT_CUSTODY")
    fd = os.open(OWNER_KEY, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o600) and
                info.st_size == 32, "OWNER_KEY_CUSTODY")
        key = file.read(33)
    require(len(key) == 32, "OWNER_KEY_SIZE")
    return key


def grant_bytes():
    fd = os.open(GRANT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o600) and
                1 <= info.st_size <= 65536, "GRANT_FILE_CUSTODY")
        data = file.read(65537)
    require(1 <= len(data) <= 65536, "GRANT_FILE_SIZE")
    return data


def verify_review():
    require(sha(BASE / "REVIEW_MANIFEST_20260926_11.json") == V11_SHA, "V11_DIGEST")
    v12 = parse_json(V12_MANIFEST.read_text())
    v12_sha = sha(V12_MANIFEST)
    require(v12["schema"] == "orion.host_profile_v2.review_manifest.v12" and
            v12["predecessor_manifest_sha256"] == V11_SHA and
            v12["host_application_authorized"] is False and
            v12["collision_clearance"] is False and
            v12["file_sha256"]["apply-host-profile-v2.py"] ==
            sha(BASE / "apply-host-profile-v2-v12.py") and
            v12["file_sha256"]["HOST_PROFILE_V2_DISTINCT_PAIR_REVIEW.json"] == PROFILE_SHA,
            "V12_REVIEW_BINDING")
    for name, digest in v12["file_sha256"].items():
        archived = {"apply-host-profile-v2.py": "apply-host-profile-v2-v12.py",
                    "test_apply_host_profile_v2_failures.py":
                    "test_apply_host_profile_v2_failures-v12.py"}.get(name, name)
        require(sha(BASE / archived) == digest, "V12_FILE_DIGEST")
    v13 = parse_json(V13_MANIFEST.read_text())
    v13_sha = sha(V13_MANIFEST)
    require(v13["schema"] == "orion.host_profile_v2.review_manifest.v13" and
            v13["predecessor_manifest_sha256"] == v12_sha and
            v13["host_application_authorized"] is False and
            v13["collision_clearance"] is False and
            v13["file_sha256"]["apply-host-profile-v2.py"] ==
            sha(BASE / "apply-host-profile-v2-v13.py"),
            "V13_REVIEW_BINDING")
    for name, digest in v13["file_sha256"].items():
        archived = {"apply-host-profile-v2.py": "apply-host-profile-v2-v13.py",
                    "test_apply_host_profile_v2_systemctl.py":
                    "test_apply_host_profile_v2_systemctl-v13.py"}.get(name, name)
        require(sha(BASE / archived) == digest, "V13_FILE_DIGEST")
    v14 = parse_json(V14_MANIFEST.read_text())
    v14_sha = sha(V14_MANIFEST)
    require(v14["schema"] == "orion.host_profile_v2.review_manifest.v14" and
            v14["predecessor_manifest_sha256"] == v13_sha and
            v14["host_application_authorized"] is False and
            v14["collision_clearance"] is False and
            v14["file_sha256"]["apply-host-profile-v2.py"] ==
            sha(BASE / "apply-host-profile-v2-v14.py"),
            "V14_REVIEW_BINDING")
    for name, digest in v14["file_sha256"].items():
        archived = {"apply-host-profile-v2.py": "apply-host-profile-v2-v14.py",
                    "test_apply_host_profile_v2_failures.py":
                    "test_apply_host_profile_v2_failures-v14.py"}.get(name, name)
        require(sha(BASE / archived) == digest, "V14_FILE_DIGEST")
    v15 = parse_json(V15_MANIFEST.read_text())
    require(v15["schema"] == "orion.host_profile_v2.review_manifest.v15" and
            v15["predecessor_manifest_sha256"] == v14_sha and
            v15["host_application_authorized"] is False and
            v15["collision_clearance"] is False and
            v15["file_sha256"]["apply-host-profile-v2.py"] == sha(Path(__file__).resolve()),
            "V15_REVIEW_BINDING")
    for name, digest in v15["file_sha256"].items():
        require(sha(BASE / name) == digest, "V15_FILE_DIGEST")
    require(sha(MANIFEST) == MANIFEST_SHA, "V9_DIGEST")
    require(sha(BASE / "HOST_PROFILE_V2_DISTINCT_PAIR_REVIEW.json") == PROFILE_SHA, "PROFILE_DIGEST")
    require(sha(BASE / "V2_RUNTIME_RECEIPT_SCHEMA_REVIEW_20260926_V2.json") == SCHEMA_SHA, "SCHEMA_DIGEST")
    require(sha(BASE / "V2_OWNER_ROUTE_POLICY_DECISION_20260926.json") == DECISION_SHA, "DECISION_DIGEST")
    require(sha(BASE / "V2_RUNTIME_RECEIPT_SCHEMA_OWNER_ACCEPTANCE_20260926.json") == ACCEPTANCE_SHA,
            "ACCEPTANCE_DIGEST")
    manifest = parse_json(MANIFEST.read_text())
    require(manifest["host_application_authorized"] is False and manifest["collision_clearance"] is False,
            "REVIEW_AUTHORITY_DRIFT")
    require(manifest["owner_receipt_schema_format_approved"] is True and
            manifest["owner_route_policy_approved"] is True, "REVIEW_DECISION_MISSING")
    for version in (11, 10, 9, 8, 7, 6, 5, 4):
        record = parse_json((BASE / f"REVIEW_MANIFEST_20260926_{version}.json").read_text())
        for name, digest in record["file_sha256"].items():
            if name == "apply-host-profile-v2.py":
                require(sha(BASE / "apply-host-profile-v2-v11.py") == digest, "PRIOR_SCRIPT_BINDING")
                continue
            if name in ("test_apply_host_profile_v2.py", "test_apply_host_profile_v2_rollback.py"):
                require(sha(BASE / name.replace(".py", "-v11.py")) == digest, "PRIOR_TEST_BINDING")
                continue
            require(sha(BASE / name) == digest, "REVIEW_CHAIN_DIGEST")
    profile = parse_json((BASE / "HOST_PROFILE_V2_DISTINCT_PAIR_REVIEW.json").read_text())
    schema = parse_json((BASE / "V2_RUNTIME_RECEIPT_SCHEMA_REVIEW_20260926_V2.json").read_text())
    decision = parse_json((BASE / "V2_OWNER_ROUTE_POLICY_DECISION_20260926.json").read_text())
    require(decision["status"] == "ROUTE_POLICY_APPROVED_REVIEW_ONLY", "ROUTE_DECISION")
    acceptance = parse_json((BASE / "V2_RUNTIME_RECEIPT_SCHEMA_OWNER_ACCEPTANCE_20260926.json").read_text())
    require(acceptance["status"] == "SCHEMA_FORMAT_APPROVED_ONLY" and
            acceptance["decision"]["host_application_authorized"] is False, "SCHEMA_ACCEPTANCE_SCOPE")
    require(schema["source_bindings"]["v7_manifest"] == V7_SHA, "SCHEMA_V7_BINDING")
    require(schema["source_bindings"]["owner_route_decision"] == DECISION_SHA, "SCHEMA_DECISION_BINDING")
    synthetic = parse_json((BASE / "V2_SYNTHETIC_OUTER_ROUTE_DELTA_REVIEW_20260926.json").read_text())
    require(sha(BASE / "V2_SYNTHETIC_OUTER_ROUTE_DELTA_REVIEW_20260926.json") == SYNTHETIC_SHA and
            schema["approved_host_delta_exact_objects"] ==
            synthetic["link_and_address_delta_after_loopback_up"]["added"], "APPROVED_DELTA_BINDING")
    require(sha(BASE / "HOST_APPLICATION_V2_PROCEDURE_REVIEW.md") == PROCEDURE_SHA, "PROCEDURE_DIGEST")
    require(sha(ROOT / "host-isolated-network.sh") == NETWORK_SHA, "POLICY_DIGEST")
    return profile, schema


def policy_tuple():
    source = (ROOT / "host-isolated-network.sh").read_text()
    address = re.findall(r"^address=([^\n]+)$", source, re.M)
    hostname = re.findall(r"^hostname=([^\n]+)$", source, re.M)
    require(len(address) == len(hostname) == 1, "POLICY_TUPLE_SHAPE")
    def literal(value):
        require(re.fullmatch(r"[A-Za-z0-9.-]+", value) is not None, "POLICY_TUPLE_LITERAL")
        return value
    address, hostname = literal(address[0].strip("\"'")), literal(hostname[0].strip("\"'"))
    require(ipaddress.ip_address(address).version == 4, "POLICY_ADDRESS")
    require(sha_bytes(canonical({"address": address, "hostname": hostname})) == TUPLE_SHA, "POLICY_TUPLE_DIGEST")
    require(sha_bytes(f"{address} {hostname}\n".encode()) == HOSTS_SHA, "HOSTS_DIGEST")
    return address, hostname


def proposed_units():
    socket = (ROOT / "channel-candidate/orion-pilot-metadata.socket").read_bytes()
    template = (ROOT / "channel-candidate/reference-fixed-v6/orion-pilot-metadata@.service").read_bytes()
    require(sha_bytes(socket) == OLD_SOCKET_SHA and sha_bytes(template) == OLD_TEMPLATE_SHA, "UNIT_SOURCE_DIGEST")
    transforms = [
        (b"ORION root-only exact-session operator channel (candidate, not installed)", b"ORION root-only exact-session operator channel v2"),
        (b"/run/orion-pilot/operator.sock", b"/run/orion-pilot-v2/operator.sock"),
    ]
    for old, new in transforms:
        require(socket.count(old) == 1, "SOCKET_TRANSFORM")
        socket = socket.replace(old, new)
    transforms = [
        (b"ORION bounded metadata pilot (manual exact-session activation only)", b"ORION bounded metadata pilot (socket-activated exact-session v2)"),
        (b"orion-pilot-f7c7955", NS.encode()),
        (b"orion-metadata-133d6533f23532585dd9f385", SESSION.encode()),
    ]
    for old, new in transforms:
        require(template.count(old) >= 1, "TEMPLATE_TRANSFORM")
        template = template.replace(old, new)
    require(sha_bytes(socket) == NEW_SOCKET_SHA and sha_bytes(template) == NEW_TEMPLATE_SHA, "UNIT_OUTPUT_DIGEST")
    return socket, template


def nft_match(left, right, op="=="):
    return {"match": {"left": left, "op": op, "right": right}}


def meta(key):
    return {"meta": {"key": key}}


def payload(proto, field):
    return {"payload": {"protocol": proto, "field": field}}


def nft_expected(address):
    accept = {"accept": None}
    state = nft_match({"ct": {"key": "state"}}, ["established", "related"], "in")
    nat = [
        {"table": {"family": "ip", "name": NAT}},
        {"chain": {"family": "ip", "table": NAT, "name": "postrouting", "type": "nat",
                   "hook": "postrouting", "prio": 101, "policy": "accept"}},
        {"rule": {"family": "ip", "table": NAT, "chain": "postrouting", "expr": [
            nft_match(payload("ip", "saddr"), PILOT_IP),
            nft_match(payload("ip", "daddr"), address),
            nft_match(meta("oifname"), "eth0"), {"masquerade": None}]}}
    ]
    filt = [
        {"table": {"family": "inet", "name": FILTER}},
        {"chain": {"family": "inet", "table": FILTER, "name": "output", "type": "filter",
                   "hook": "output", "prio": -50, "policy": "drop"}},
        {"chain": {"family": "inet", "table": FILTER, "name": "input", "type": "filter",
                   "hook": "input", "prio": -50, "policy": "drop"}},
        {"rule": {"family": "inet", "table": FILTER, "chain": "output",
                  "expr": [nft_match(meta("oifname"), "lo"), accept]}},
        {"rule": {"family": "inet", "table": FILTER, "chain": "output",
                  "expr": [nft_match(payload("ip", "daddr"), address),
                           nft_match(payload("tcp", "dport"), 443), accept]}},
        {"rule": {"family": "inet", "table": FILTER, "chain": "input",
                  "expr": [nft_match(meta("iifname"), "lo"), accept]}},
        {"rule": {"family": "inet", "table": FILTER, "chain": "input",
                  "expr": [state, accept]}}
    ]
    host_out = {"rule": {"family": "ip", "table": "filter", "chain": "DOCKER-USER",
                         "comment": COMMENT, "expr": [
        nft_match(meta("iifname"), H), nft_match(meta("oifname"), "eth0"),
        nft_match(payload("ip", "saddr"), PILOT_IP),
        nft_match(payload("ip", "daddr"), address),
        nft_match(payload("tcp", "dport"), 443), accept]}}
    host_in = {"rule": {"family": "ip", "table": "filter", "chain": "DOCKER-USER",
                        "comment": COMMENT, "expr": [
        nft_match(meta("iifname"), "eth0"), nft_match(meta("oifname"), H),
        nft_match(payload("ip", "saddr"), address),
        nft_match(payload("ip", "daddr"), PILOT_IP), state, accept]}}
    return nat, filt, host_out, host_in


def normalize_nft(value, *, baseline=False):
    if isinstance(value, list):
        return [normalize_nft(item, baseline=baseline) for item in value]
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if key == "handle":
                continue
            if baseline and key == "counter" and isinstance(item, dict):
                require(set(item) == {"bytes", "packets"}, "NFT_COUNTER_SHAPE")
                clean[key] = {"bytes": "dynamic", "packets": "dynamic"}
            else:
                clean[key] = normalize_nft(item, baseline=baseline)
        return clean
    return value


def nft_entries(value, *, baseline=False):
    require(type(value) is dict and set(value) == {"nftables"}, "NFT_JSON_SHAPE")
    return [normalize_nft(row, baseline=baseline) for row in value["nftables"]
            if "metainfo" not in row]


def nft_rules(value):
    return [row["rule"] for row in value["nftables"] if "rule" in row]


def systemd_state(unit, *, check_label=None):
    output = run([SYSTEMCTL, "show", "--no-pager", unit,
                  "--property=LoadState,ActiveState,UnitFileState,SubState"],
                 systemctl_check_label=check_label)
    state = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    require({"LoadState", "ActiveState", "UnitFileState", "SubState"} <= set(state), "SYSTEMD_STATE_SHAPE")
    return state


def v2_unit_file_rows(*, allow_staged):
    """systemd v255 uses rc=1 for zero matched unit files; corroborate before accepting."""
    pattern = "orion-pilot-metadata-v2@" + "*.service"
    argv = [SYSTEMCTL, "list-unit-files", "--no-legend", "--no-pager", pattern]
    result = subprocess.run(argv, text=True, capture_output=True, timeout=15, check=False)

    def names(output):
        rows = [line.split() for line in output.splitlines() if line.strip()]
        require(all(len(row) >= 2 for row in rows), "V2_UNIT_FILE_LIST_SHAPE")
        return [row[0] for row in rows]

    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        # An unfiltered successful listing distinguishes v255's empty-pattern
        # ENOENT from an unavailable or failing systemd manager.
        all_files = run([SYSTEMCTL, "list-unit-files", "--no-legend", "--no-pager"],
                        systemctl_check_label="VERIFY_V2_UNIT_FILES")
        listed = names(all_files)
        require(listed, "V2_UNIT_FILE_ABSENCE_UNCONFIRMED")
        require(not any(name.startswith("orion-pilot-metadata-v2@") and
                        name.endswith(".service") for name in listed),
                "V2_UNIT_FILE_COLLISION")
        require(not allow_staged, "V2_STAGED_UNIT_FILE_MISSING")
        return []
    if result.returncode != 0:
        raise Blocked(f"SYSTEMCTL_LIST_V2_INSTANCE_FILES_RC_{result.returncode}")
    rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
    require(rows and all(len(row) >= 2 and row[0].startswith("orion-pilot-metadata-v2@") and
                         row[0].endswith(".service") for row in rows),
            "V2_UNIT_FILE_LIST_UNCONFIRMED")
    require(allow_staged and len(rows) == 1 and rows[0][0] == NEW_TEMPLATE and
            rows[0][1] == "static", "V2_UNIT_FILE_COLLISION")
    return rows


def instance_names(prefix, *, allow_staged_v2=False):
    pattern = prefix + "*.service"  # Literal argv wildcard: no backslash.
    label = {"orion-pilot-metadata@": "OLD", "orion-pilot-metadata-v2@": "V2"}.get(prefix)
    require(label is not None, "INSTANCE_PREFIX")
    if label == "V2":
        v2_unit_file_rows(allow_staged=allow_staged_v2)
    else:
        run([SYSTEMCTL, "list-unit-files", "--no-legend", "--no-pager", pattern],
            systemctl_check_label=f"LIST_{label}_INSTANCE_FILES")
    output = run([SYSTEMCTL, "list-units", "--all", "--plain", "--no-legend",
                  "--no-pager", "--full", pattern],
                 systemctl_check_label=f"LIST_{label}_INSTANCES")
    return [line.split()[0] for line in output.splitlines() if line.strip()]


def stopped_units(*, allow_staged_v2=False):
    old = systemd_state(OLD_SOCKET, check_label="SHOW_OLD_SOCKET")
    new = systemd_state(NEW_SOCKET, check_label="SHOW_V2_SOCKET")
    plain = systemd_state("orion-pilot-metadata.service", check_label="SHOW_PLAIN_SERVICE")
    require(old["ActiveState"] == "inactive" and old["UnitFileState"] == "disabled", "OLD_SOCKET_STATE")
    require(new["LoadState"] == "not-found" or
            (new["ActiveState"] == "inactive" and new["UnitFileState"] == "disabled"), "V2_SOCKET_STATE")
    require(plain["ActiveState"] != "active" and plain["UnitFileState"] in ("disabled", "static", ""), "PLAIN_SERVICE_STATE")
    for prefix in ("orion-pilot-metadata@", "orion-pilot-metadata-v2@"):
        for name in instance_names(prefix, allow_staged_v2=allow_staged_v2):
            label = "SHOW_OLD_INSTANCE" if prefix == "orion-pilot-metadata@" else "SHOW_V2_INSTANCE"
            require(systemd_state(name, check_label=label)["ActiveState"] != "active",
                    "ACTIVE_TEMPLATE_INSTANCE")
    require(absent(SOCKET_PATH), "V2_SOCKET_NODE")
    return {"old_socket_stopped_disabled": True, "v2_socket_absent_or_stopped_disabled": True,
            "active_old_instance_count": 0, "active_v2_instance_count": 0}


def historical_fingerprint(paths):
    rows = []
    for path in paths:
        root = Path(path)
        require(not absent(root), "HISTORICAL_PATH_MISSING")
        for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
            dirs.sort(); files.sort()
            for name in [".", *dirs, *files]:
                item = Path(current) if name == "." else Path(current) / name
                info = item.lstat()
                payload = [str(item), info.st_mode, info.st_uid, info.st_gid, info.st_nlink]
                if stat.S_ISREG(info.st_mode):
                    payload.append(sha(item))
                elif stat.S_ISLNK(info.st_mode):
                    payload.append(os.readlink(item))
                rows.append(payload)
            if not root.is_dir():
                break
        if root.is_file() or root.is_symlink():
            info = root.lstat()
            rows.append([str(root), info.st_mode, info.st_uid, info.st_gid,
                         info.st_nlink, sha(root) if stat.S_ISREG(info.st_mode) else os.readlink(root)])
    return sha_bytes(canonical(rows))


def protected_paths(profile):
    return [*profile["protected_historical_paths"], "/var/lib/orion-pilot/sessions"]


def collision_routes(routes):
    for key, rows in routes.items():
        for row in rows:
            require(row.get("dev") not in (H, P), "ROUTE_LINK_COLLISION")
            for field in ("dst", "gateway", "prefsrc", "src"):
                value = row.get(field)
                if value and value != "default":
                    try:
                        entity = ipaddress.ip_network(value, strict=False)
                        if entity.version == 4:
                            require(not entity.overlaps(NETWORK), "ROUTE_SUBNET_COLLISION")
                    except ValueError:
                        raise Blocked("UNKNOWN_ROUTE_ADDRESS") from None


def collisions(routes):
    for path in (NS_PATH, MARKER, NETWORK_MARKER, RESOLVER_DIR, RECEIPTS.parent,
                 UNITS / NEW_SOCKET, UNITS / NEW_TEMPLATE, SOCKET_PATH):
        require(absent(path), "V2_PATH_COLLISION")
    links = json_command([IP, "-j", "link", "show"])
    require(not any(row.get("ifname") in (H, P) for row in links), "LINK_COLLISION")
    addresses = json_command([IP, "-j", "address", "show"])
    for link in addresses:
        for addr in link.get("addr_info", []):
            if addr.get("family") == "inet":
                value = ipaddress.ip_address(addr["local"])
                require(value not in NETWORK, "ADDRESS_COLLISION")
    collision_routes(routes)
    ruleset = nft_json("list", "ruleset")
    for item in ruleset["nftables"]:
        for kind in ("table", "chain", "rule", "set", "map"):
            value = item.get(kind, {})
            require(value.get("table") not in (NAT, FILTER) and value.get("name") not in (NAT, FILTER)
                    and value.get("comment") != COMMENT, "NFT_COLLISION")
    nft_json("list", "chain", "ip", "filter", "DOCKER-USER")
    return True


def host_snapshot(profile, address):
    routes = route_arrays()
    collisions(routes)
    units = stopped_units()
    require(Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "1", "HOST_FORWARDING")
    lookup = json_command([IP, "-j", "route", "get", address])
    require(len(lookup) == 1 and lookup[0].get("dev") == "eth0", "HOST_EGRESS_ROUTE")
    old_units = {str(UNITS / OLD_SOCKET): OLD_SOCKET_SHA,
                 str(UNITS / OLD_TEMPLATE): OLD_TEMPLATE_SHA}
    for name, digest in old_units.items():
        strict_file(Path(name), 0, 0, 0o644, digest)
    sessions = Path("/var/lib/orion-pilot/sessions")
    s = sessions.lstat()
    require(stat.S_ISDIR(s.st_mode) and (s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)) ==
            (0, 989, 0o750), "SESSIONS_PARENT_CUSTODY")
    interpreter = Path(profile["service_binding"]["interpreter_path"])
    require(interpreter.is_symlink() and sha(interpreter.resolve()) == INTERPRETER_SHA,
            "INTERPRETER_CUSTODY")
    resolved = interpreter.resolve().stat()
    require(resolved.st_uid == 0 and bool(resolved.st_mode & 0o111), "INTERPRETER_CUSTODY")
    protected = historical_fingerprint(protected_paths(profile))
    docker = nft_json("list", "chain", "ip", "filter", "DOCKER-USER")
    return {"routes": routes, "docker": docker, "historical": protected, "units": units,
            "review_digest": MANIFEST_SHA}


def verify_grant():
    require(os.geteuid() == 0, "ROOT_REQUIRED")
    parent = GRANT.parent.lstat()
    require(stat.S_ISDIR(parent.st_mode) and
            (parent.st_uid, parent.st_gid, stat.S_IMODE(parent.st_mode)) == (0, 989, 0o750),
            "GRANT_PARENT_CUSTODY")
    raw_grant = grant_bytes()
    grant = parse_json(raw_grant.decode("utf-8"))
    required = {"schema", "status", "scope", "session_id", "manifest_v9_sha256",
                "manifest_v11_sha256", "manifest_v12_sha256", "manifest_v15_sha256",
                "profile_sha256", "script_sha256", "grant_id",
                "expires_at_utc", "owner_declaration", "approval"}
    require(set(grant) == required, "GRANT_SHAPE")
    subject = {name: value for name, value in grant.items() if name != "approval"}
    require(grant["schema"] == "orion.host_profile_v2.host_application_grant.v1"
            and grant["status"] == "OWNER_AUTHORIZED_HOST_APPLICATION"
            and grant["scope"] == "host_application_only"
            and grant["session_id"] == SESSION
            and grant["manifest_v9_sha256"] == MANIFEST_SHA
            and grant["manifest_v11_sha256"] == V11_SHA
            and grant["manifest_v12_sha256"] == sha(V12_MANIFEST)
            and grant["manifest_v15_sha256"] == sha(V15_MANIFEST)
            and grant["profile_sha256"] == PROFILE_SHA
            and grant["script_sha256"] == sha(Path(__file__).resolve()), "GRANT_BINDING")
    require(type(grant["grant_id"]) is str and re.fullmatch(r"[0-9a-f]{32}", grant["grant_id"]) is not None,
            "GRANT_ID")
    require(type(grant["owner_declaration"]) is str and
            grant["owner_declaration"] ==
            "I authorize one stopped, unqualified host-profile v2 application for " + SESSION +
            " bound to the stated manifest and script digests. No DNS/TLS or customer activity is authorized.",
            "GRANT_DECLARATION")
    try:
        expiry = dt.datetime.strptime(grant["expires_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        raise Blocked("GRANT_EXPIRY_FORMAT") from None
    now = dt.datetime.now(dt.timezone.utc)
    require(now < expiry <= now + dt.timedelta(hours=1), "GRANT_EXPIRY_WINDOW")
    approval = grant["approval"]
    require(type(approval) is dict and set(approval) ==
            {"version", "purpose", "subject_sha256", "controller_id", "issued_at", "expires_at", "mac"},
            "OWNER_APPROVAL_SHAPE")
    payload = {name: value for name, value in approval.items() if name != "mac"}
    require(approval["version"] == APPROVAL_VERSION and approval["purpose"] == APPROVAL_PURPOSE and
            approval["subject_sha256"] == sha_bytes(json.dumps(subject, sort_keys=True,
                                            separators=(",", ":"), allow_nan=False).encode()),
            "OWNER_APPROVAL_SUBJECT")
    require(type(approval["controller_id"]) is str and
            re.fullmatch(r"[!-~]{1,128}", approval["controller_id"]) is not None,
            "OWNER_CONTROLLER_ID")
    try:
        issued = dt.datetime.fromisoformat(approval["issued_at"])
        approved_until = dt.datetime.fromisoformat(approval["expires_at"])
        require(issued.tzinfo is not None and approved_until.tzinfo is not None,
                "OWNER_APPROVAL_TIMEZONE")
    except (TypeError, ValueError):
        raise Blocked("OWNER_APPROVAL_TIME") from None
    require(issued <= now < approved_until <= expiry and
            dt.timedelta(0) < approved_until - issued <= dt.timedelta(hours=24),
            "OWNER_APPROVAL_EXPIRY")
    # Same local-broker-v3 HMAC domain and purpose binding as the installed
    # trusted-controller approval contract. Missing custody/key blocks closed.
    key = owner_key_bytes()
    expected = hmac.new(key, json.dumps((BROKER_VERSION, APPROVAL_PURPOSE, payload),
                        sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
                        hashlib.sha256).hexdigest()
    require(type(approval["mac"]) is str and hmac.compare_digest(approval["mac"], expected),
            "OWNER_APPROVAL_AUTHENTICATION")
    marker = Path("/etc/orion-pilot") / ("host-profile-v2-grant-used-" + grant["grant_id"])
    require(absent(marker), "GRANT_ALREADY_USED")
    return grant, sha_bytes(raw_grant), marker


def create_file(path: Path, data: bytes, uid: int, gid: int, mode: int, created):
    require(absent(path), "EXCLUSIVE_FILE_COLLISION")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    pending = {"kind": "file_pending", "path": path, "inode": None}
    created.append(pending)
    with os.fdopen(fd, "wb") as file:
        pending["inode"] = os.fstat(file.fileno()).st_ino
        os.fchown(file.fileno(), uid, gid)
        os.fchmod(file.fileno(), mode)
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    info = strict_file(path, uid, gid, mode, sha_bytes(data))
    require(info.st_ino == pending["inode"], "FILE_REPLACED")
    created[-1] = ("file", path, info.st_ino, sha_bytes(data), uid, gid, mode)


def create_dir(path: Path, uid: int, gid: int, mode: int, created):
    require(absent(path), "EXCLUSIVE_DIR_COLLISION")
    os.mkdir(path, mode)
    pending = {"kind": "dir_pending", "path": path, "inode": None}
    created.append(pending)
    pending["inode"] = path.lstat().st_ino
    os.chown(path, uid, gid, follow_symlinks=False)
    os.chmod(path, mode, follow_symlinks=False)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and
            (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (uid, gid, mode), "DIR_CUSTODY")
    require(info.st_ino == pending["inode"], "DIR_REPLACED")
    created[-1] = ("dir", path, info.st_ino, uid, gid, mode)


def namespace_inode():
    value = run([IP, "netns", "exec", NS, "/usr/bin/readlink", "/proc/self/ns/net"]).strip()
    require(re.fullmatch(r"net:\[[0-9]+\]", value) is not None, "NAMESPACE_INODE")
    return value


def namespace_forwarding_zero():
    code = ("from pathlib import Path; p=['/proc/sys/net/ipv4/ip_forward',"
            "'/proc/sys/net/ipv6/conf/all/forwarding']; "
            "[Path(x).write_text('0\\n') for x in p]; "
            "print(','.join(Path(x).read_text().strip() for x in p))")
    value = run([IP, "netns", "exec", NS, "/usr/bin/python3", "-B", "-c", code]).strip()
    require(value == "0,0", "NAMESPACE_FORWARDING")


def namespace_forwarding_read():
    code = ("from pathlib import Path; p=['/proc/sys/net/ipv4/ip_forward',"
            "'/proc/sys/net/ipv6/conf/all/forwarding']; "
            "print(','.join(Path(x).read_text().strip() for x in p))")
    value = run([IP, "netns", "exec", NS, "/usr/bin/python3", "-B", "-c", code]).strip()
    require(value == "0,0", "NAMESPACE_FORWARDING_DRIFT")


def link_index(name, *, namespace=False):
    links = ns_json("link", "show", "dev", name) if namespace else json_command([IP, "-j", "link", "show", "dev", name])
    require(len(links) == 1 and links[0].get("ifname") == name, "LINK_IDENTITY")
    return links[0]["ifindex"]


def verify_veth_pair(host_idx, pilot_idx, ns_inode, *, peer_in_namespace=True):
    require(namespace_inode() == ns_inode, "VETH_NAMESPACE_IDENTITY")
    host = json_command([IP, "-j", "-details", "link", "show", "dev", H])
    peer = (ns_json("-details", "link", "show", "dev", P) if peer_in_namespace else
            json_command([IP, "-j", "-details", "link", "show", "dev", P]))
    require(len(host) == len(peer) == 1, "VETH_PAIR_COUNT")
    a, b = host[0], peer[0]
    require(a.get("ifname") == H and b.get("ifname") == P and
            a.get("ifindex") == host_idx and b.get("ifindex") == pilot_idx and
            a.get("linkinfo", {}).get("info_kind") == "veth" and
            b.get("linkinfo", {}).get("info_kind") == "veth" and
            a.get("link_index") == pilot_idx and b.get("link_index") == host_idx,
            "VETH_PAIR_IDENTITY")


def create_namespace(created):
    run([IP, "netns", "add", NS])
    pending = {"kind": "namespace_pending", "path_inode": None}
    created.append(pending)
    pending["path_inode"] = NS_PATH.lstat().st_ino
    inode = namespace_inode()
    created[-1] = ("namespace", inode, pending["path_inode"])
    return inode


def create_veth(ns_inode, created):
    run([IP, "link", "add", H, "type", "veth", "peer", "name", P])
    pending = {"kind": "veth_pending", "namespace_inode": ns_inode,
               "host_idx": None, "pilot_idx": None}
    created.append(pending)
    host_idx, pilot_idx = link_index(H), link_index(P)
    pending["host_idx"], pending["pilot_idx"] = host_idx, pilot_idx
    verify_veth_pair(host_idx, pilot_idx, ns_inode, peer_in_namespace=False)
    created[-1] = ("veth", host_idx, pilot_idx, ns_inode)
    return host_idx, pilot_idx


def add_table(family, name, *, namespace, created):
    nft_run("add", "table", family, name, namespace=namespace)
    pending = {"kind": "nft_table_pending", "family": family, "name": name,
               "namespace": namespace, "handle": None}
    created.append(pending)
    value = nft_json("list", "table", family, name, namespace=namespace)
    table = next((row["table"] for row in value["nftables"] if "table" in row), None)
    if table is not None:
        pending["handle"] = table.get("handle")
    require(table is not None and table["name"] == name and table["family"] == family, "NFT_TABLE_IDENTITY")
    created[-1] = ("nft_table", family, name, namespace, table["handle"])


def add_docker_rule(tokens, expected, created):
    before = nft_json("list", "chain", "ip", "filter", "DOCKER-USER")
    prior = {row["handle"] for row in nft_rules(before)}
    nft_run("insert", "rule", "ip", "filter", "DOCKER-USER", *tokens)
    pending = {"kind": "docker_rule_pending", "prior_handles": prior,
               "expected": expected, "handle": None}
    created.append(pending)
    after = nft_json("list", "chain", "ip", "filter", "DOCKER-USER")
    candidates = [row for row in nft_rules(after) if row["handle"] not in prior]
    if len(candidates) == 1:
        pending["handle"] = candidates[0]["handle"]
    require(len(candidates) == 1 and normalize_nft({"rule": candidates[0]}) == expected,
            "NFT_NEW_RULE_IDENTITY")
    created[-1] = ("docker_rule", candidates[0]["handle"], expected)


def install_nft(address, created):
    expected_nat, expected_filter, expected_out, expected_in = nft_expected(address)
    add_table("inet", FILTER, namespace=True, created=created)
    nft_run("add", "chain", "inet", FILTER, "output",
            "{ type filter hook output priority -50; policy drop; }", namespace=True)
    nft_run("add", "chain", "inet", FILTER, "input",
            "{ type filter hook input priority -50; policy drop; }", namespace=True)
    for args in (("output", "oifname", "lo", "accept"),
                 ("output", "ip", "daddr", address, "tcp", "dport", "443", "accept"),
                 ("input", "iifname", "lo", "accept"),
                 ("input", "ct", "state", "established,related", "accept")):
        nft_run("add", "rule", "inet", FILTER, *args, namespace=True)
    add_table("ip", NAT, namespace=False, created=created)
    nft_run("add", "chain", "ip", NAT, "postrouting",
            "{ type nat hook postrouting priority 101; policy accept; }")
    nft_run("add", "rule", "ip", NAT, "postrouting", "ip", "saddr", PILOT_IP,
            "ip", "daddr", address, "oifname", "eth0", "masquerade")
    add_docker_rule(("iifname", H, "oifname", "eth0", "ip", "saddr", PILOT_IP,
                     "ip", "daddr", address, "tcp", "dport", "443", "accept",
                     "comment", COMMENT), expected_out, created)
    add_docker_rule(("iifname", "eth0", "oifname", H, "ip", "saddr", address,
                     "ip", "daddr", PILOT_IP, "ct", "state", "established,related",
                     "accept", "comment", COMMENT), expected_in, created)
    return expected_nat, expected_filter, expected_out, expected_in


def verify_nft(address, baseline):
    nat, filt, host_out, host_in = nft_expected(address)
    actual_nat = nft_entries(nft_json("list", "table", "ip", NAT))
    actual_filt = nft_entries(nft_json("list", "table", "inet", FILTER, namespace=True))
    require(actual_nat == nat and actual_filt == filt, "NFT_TABLE_EXACT")
    host = nft_entries(nft_json("list", "chain", "ip", "filter", "DOCKER-USER"), baseline=True)
    old = nft_entries(baseline, baseline=True)
    require(host == [old[0], host_in, host_out, *old[1:]], "DOCKER_USER_EXACT")
    return sha_bytes(canonical({"nat": nat, "filter": filt,
                                "docker_added": [host_in, host_out]}))


def verify_routes(profile, schema, before):
    ns_routes = route_arrays(namespace=True)
    host_after = route_arrays()
    approved = schema["approved_host_delta_exact_objects"]
    for key in ROUTE_KEYS:
        require(multiset(ns_routes[key]) == multiset(profile["expected_routes"][key]), "NAMESPACE_ROUTE_EXACT")
        prior, later = multiset(before[key]), multiset(host_after[key])
        require(not (prior - later), "HOST_ROUTE_REMOVED")
        require(later - prior == multiset(approved[key]), "HOST_ROUTE_DELTA_EXACT")
    return ns_routes, host_after


def verify_staged_files(address, created):
    for item in created:
        if item[0] == "file":
            _, path, inode, digest, uid, gid, mode = item
            info = strict_file(path, uid, gid, mode, digest)
            require(info.st_ino == inode, "FILE_REPLACED")
    require(sha(RESOLVER) == HOSTS_SHA, "RESOLVER_BYTES")
    inode = namespace_inode()
    require(MARKER.read_text() == inode + "\n" and NETWORK_MARKER.read_text() == inode + "\n",
            "MARKER_INODE")
    lookup = json_command([IP, "-j", "route", "get", address])
    require(len(lookup) == 1 and lookup[0].get("dev") == "eth0", "EGRESS_INTERFACE")


def one_shot_marker(path, grant_sha, created):
    data = (json.dumps({"grant_sha256": grant_sha, "status": "CONSUMED_FOR_ONE_HOST_APPLICATION_ATTEMPT"},
                       sort_keys=True, separators=(",", ":")) + "\n").encode()
    # This v2-owned audit marker is intentionally retained after rollback.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    pending = {"kind": "grant_marker_pending", "path": path, "inode": None,
               "digest": sha_bytes(data)}
    created.append(pending)
    with os.fdopen(fd, "wb") as file:
        pending["inode"] = os.fstat(file.fileno()).st_ino
        os.fchown(file.fileno(), 0, 0)
        os.fchmod(file.fileno(), 0o600)
        file.write(data); file.flush(); os.fsync(file.fileno())
    info = strict_file(path, 0, 0, 0o600, sha_bytes(data))
    require(info.st_ino == pending["inode"], "GRANT_MARKER_REPLACED")
    created[-1] = ("grant_marker", path, info.st_ino, sha_bytes(data))
    return path


def receipt_dirs(created):
    parent = Path("/var/lib/orion-pilot")
    info = parent.lstat()
    require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
            (0, 989, 0o750), "RECEIPT_PARENT_CUSTODY")
    create_dir(RECEIPTS.parent, 0, 0, 0o700, created)
    create_dir(RECEIPTS, 0, 0, 0o700, created)


def receipt_snapshot(kind, rows, before=None, approved=None):
    if kind == "namespace":
        return {"count": len(rows), "canonical_multiset_sha256": multiset_hash(rows),
                "objects": rows, "exact_match": True}
    require(before is not None and approved is not None, "HOST_RECEIPT_INPUT")
    old, new = multiset(before), multiset(rows)
    added = []
    remaining = old.copy()
    for row in rows:
        encoded = canonical(row)
        if remaining[encoded]:
            remaining[encoded] -= 1
        else:
            added.append(row)
    require(not any(remaining.values()) and multiset(added) == multiset(approved), "HOST_RECEIPT_DELTA")
    return {"before_count": len(before), "before_multiset_sha256": multiset_hash(before),
            "after_count": len(rows), "after_multiset_sha256": multiset_hash(rows),
            "unchanged_baseline": True, "removed_count": 0, "added_count": len(added),
            "added_objects": added, "added_multiset_sha256": multiset_hash(added),
            "approved_delta_exact_match": True}


def v2_file_meta(created, grant_marker):
    paths = [item for item in created if item[0] == "file"]
    info = grant_marker.lstat()
    paths.append(("file", grant_marker, info.st_ino, sha(grant_marker), 0, 0, 0o600))
    output = []
    for _, path, inode, digest, uid, gid, mode in paths:
        observed = strict_file(path, uid, gid, mode, digest)
        require(observed.st_ino == inode, "V2_FILE_CHANGED")
        output.append({"path": str(path), "sha256": digest, "kind": "regular_file",
                       "uid": uid, "gid": gid, "mode": f"{mode:04o}", "link_count": 1})
    return output


def build_receipt(grant, grant_sha, grant_marker, profile, schema, created,
                  preflight, ns_routes, host_before, host_after, nft_digest):
    inode = namespace_inode()
    historical_after = historical_fingerprint(protected_paths(profile))
    require(historical_after == preflight["historical"], "HISTORICAL_ARTIFACT_CHANGED")
    policy = schema["receipt_json_schema"]
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    receipt = {
        "schema": "orion.host_profile_v2.application_receipt.v2",
        "record_kind": "original", "outcome": "STAGED_UNQUALIFIED_PENDING_REVIEW",
        "recorded_at_utc": now, "transaction_id": secrets.token_hex(16),
        "correction_of": None, "evidence_class": "PRODUCTION_RUNTIME_OBSERVATION",
        "bindings": {
            "review_manifest_v7_sha256": V7_SHA,
            "owner_route_decision_sha256": DECISION_SHA,
            "synthetic_outer_delta_sha256": SYNTHETIC_SHA,
            "review_profile_sha256": PROFILE_SHA,
            "procedure_sha256": PROCEDURE_SHA,
            "retained_network_script_sha256": NETWORK_SHA,
            "namespace_probe_line_sha256": profile["route_evidence"]["complete_pass_json_line_sha256"],
            "review_manifest_v8_sha256": V8_SHA,
            "this_schema_proposal_sha256": SCHEMA_SHA,
        },
        "authorization": {"grant_id": grant["grant_id"], "grant_sha256": grant_sha,
                          "scope": "host_application_only", "verified": True,
                          "customer_io_allowed": False},
        "session_and_resources": {
            "session_id": SESSION, "namespace_name": NS, "namespace_handle": str(NS_PATH),
            "namespace_inode": inode, "host_link": H, "pilot_link": P,
            "host_ifindex": link_index(H), "pilot_ifindex": link_index(P, namespace=True),
            "private_network": str(NETWORK), "host_nft_nat_table": NAT,
            "namespace_nft_filter_table": FILTER,
            "nft_handles": [item[-1] if item[0] == "nft_table" else item[1]
                            for item in created if item[0] in ("nft_table", "docker_rule")],
            "socket_unit": NEW_SOCKET, "service_template": NEW_TEMPLATE,
            "socket_path": str(SOCKET_PATH), "historical_artifacts_unchanged": True,
        },
        "preflight": {
            "checked_at_utc": now, "digest_chain_valid": True, "fresh_collision_clear": True,
            "namespace_and_handle_absent": True, "v2_markers_absent": True,
            "resolver_directory_absent": True, "v2_nft_objects_and_comment_absent": True,
            "v2_unit_files_absent": True, "v2_socket_node_absent": True,
            "private_30_clear": True, "link_names_clear": True,
            "old_socket_stopped_disabled": True, "v2_socket_absent_or_stopped_disabled": True,
            "active_old_instance_count": 0, "active_v2_instance_count": 0,
            "host_forwarding_expected": True, "egress_interface_expected": True,
        },
        "policy_checks": {
            "policy_tuple_sha256": TUPLE_SHA, "resolver_hosts_sha256": HOSTS_SHA,
            "resolver_custody_exact": True, "forwarding_ipv4": "0", "forwarding_ipv6": "0",
            "host_nat_table_count": 1, "namespace_filter_table_count": 1,
            "docker_user_created_rule_count": 2,
            "firewall_semantic_sha256": nft_digest, "firewall_exact": True,
            "egress_interface": "eth0", "egress_tcp_port": 443,
            "egress_eth0_tcp443_exact": True,
            "v2_socket_unit_sha256": NEW_SOCKET_SHA,
            "v2_template_unit_sha256": NEW_TEMPLATE_SHA,
            "unit_files_exact": True, "units_stopped_disabled": True,
        },
        "namespace_routes": {key: receipt_snapshot("namespace", ns_routes[key]) for key in ROUTE_KEYS},
        "host_routes": {key: receipt_snapshot("host", host_after[key], host_before[key],
                                              schema["approved_host_delta_exact_objects"][key])
                        for key in ROUTE_KEYS},
        "artifact_custody": {
            "v2_files": v2_file_meta(created, grant_marker), "v2_files_exact": True,
            "markers_match_namespace_inode": True,
            "protected_historical_before_sha256": preflight["historical"],
            "protected_historical_after_sha256": historical_after,
            "protected_historical_hashes_unchanged": True,
        },
        "boundaries": {"dns_tls_performed": False, "customer_http_performed": False,
                       "service_started": False, "packet_subjects_prepared": False,
                       "enrolled": False, "durable_two_get_budget_verified": False,
                       "authenticated_metadata_attempt_count": "unknown_not_assessed"},
        "rollback": {"status": "not_needed", "created_resource_identities_checked": True,
                     "retained_v2_resource_names": []},
    }
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(policy)
    Draft202012Validator(policy).validate(receipt)
    return receipt


def publish_receipt(receipt):
    # Directory was exclusively created by this transaction and remains root-only.
    dir_info = RECEIPTS.lstat()
    require(stat.S_ISDIR(dir_info.st_mode) and
            (dir_info.st_uid, dir_info.st_gid, stat.S_IMODE(dir_info.st_mode)) == (0, 0, 0o700),
            "RECEIPT_DIR_CUSTODY")
    dirfd = os.open(RECEIPTS, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temp = ".tmp-" + secrets.token_hex(16)
    final = "application-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(16) + ".json"
    data = canonical(receipt) + b"\n"
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
        with os.fdopen(fd, "wb") as file:
            os.fchown(file.fileno(), 0, 0)
            os.fchmod(file.fileno(), 0o600)
            file.write(data); file.flush(); os.fsync(file.fileno())
        from jsonschema import Draft202012Validator
        policy = parse_json((BASE / "V2_RUNTIME_RECEIPT_SCHEMA_REVIEW_20260926_V2.json").read_text())["receipt_json_schema"]
        Draft202012Validator(policy).validate(parse_json(data.decode("utf-8")))
        libc = ctypes.CDLL(None, use_errno=True)
        require(hasattr(libc, "renameat2"), "NO_RENAME_NOREPLACE")
        libc.renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                                   ctypes.c_char_p, ctypes.c_uint]
        libc.renameat2.restype = ctypes.c_int
        rc = libc.renameat2(dirfd, temp.encode(), dirfd, final.encode(), 1)
        if rc != 0:
            raise Blocked("RECEIPT_PUBLISH_FAILED")
        target = RECEIPTS / final
        try:
            os.fsync(dirfd)
            strict_file(target, 0, 0, 0o600, sha_bytes(data))
            require(target.read_bytes() == data, "RECEIPT_VERIFY")
        except (Blocked, OSError):
            raise PublishedReceiptError(target, sha_bytes(data)) from None
        return target, sha_bytes(data)
    finally:
        try:
            os.unlink(temp, dir_fd=dirfd)
        except FileNotFoundError:
            pass
        os.close(dirfd)


def rollback(created, address, *, preserve_receipts=False):
    expected_nat, expected_filter, _, _ = nft_expected(address)
    failures = []
    removed_units = False
    namespace_dependency_failed = False
    for item in reversed(created):
        try:
            kind = item["kind"] if isinstance(item, dict) else item[0]
            if kind == "grant_marker_pending":
                require(item["inode"] is not None, "ROLLBACK_GRANT_MARKER_UNVERIFIED")
                info = strict_file(item["path"], 0, 0, 0o600, item["digest"])
                require(info.st_ino == item["inode"], "ROLLBACK_GRANT_MARKER_IDENTITY")
            elif kind == "grant_marker":
                _, path, inode, digest = item
                info = strict_file(path, 0, 0, 0o600, digest)
                require(info.st_ino == inode, "ROLLBACK_GRANT_MARKER_IDENTITY")
            elif kind == "file_pending":
                path, inode = item["path"], item["inode"]
                require(inode is not None, "ROLLBACK_FILE_UNVERIFIED")
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and info.st_ino == inode and info.st_nlink == 1,
                        "ROLLBACK_PENDING_FILE_IDENTITY")
                path.unlink()
                removed_units |= path.parent == UNITS
            elif kind == "dir_pending":
                path, inode = item["path"], item["inode"]
                require(inode is not None, "ROLLBACK_DIR_UNVERIFIED")
                info = path.lstat()
                require(stat.S_ISDIR(info.st_mode) and info.st_ino == inode and not any(path.iterdir()),
                        "ROLLBACK_PENDING_DIR_IDENTITY")
                path.rmdir()
            elif kind == "docker_rule_pending":
                require(item["handle"] is not None, "ROLLBACK_PENDING_DOCKER_HANDLE")
                current = nft_json("list", "chain", "ip", "filter", "DOCKER-USER")
                candidates = [row for row in nft_rules(current)
                              if row["handle"] == item["handle"] and
                              row["handle"] not in item["prior_handles"]]
                require(len(candidates) == 1 and
                        normalize_nft({"rule": candidates[0]}) == item["expected"],
                        "ROLLBACK_PENDING_DOCKER_RULE_IDENTITY")
                nft_run("delete", "rule", "ip", "filter", "DOCKER-USER", "handle",
                        str(candidates[0]["handle"]))
            elif kind == "nft_table_pending":
                family, name, in_ns = item["family"], item["name"], item["namespace"]
                require(item["handle"] is not None, "ROLLBACK_PENDING_NFT_HANDLE")
                current = nft_json("list", "table", family, name, namespace=in_ns)
                tables = [row["table"] for row in current["nftables"] if "table" in row]
                require(len(tables) == 1 and tables[0].get("family") == family and
                        tables[0].get("name") == name and tables[0].get("handle") == item["handle"] and
                        all("table" in row or "metainfo" in row for row in current["nftables"]),
                        "ROLLBACK_PENDING_NFT_TABLE_IDENTITY")
                nft_run("delete", "table", family, name, namespace=in_ns)
            elif kind == "veth_pending":
                host_idx, pilot_idx = item["host_idx"], item["pilot_idx"]
                require(host_idx is not None and pilot_idx is not None, "ROLLBACK_PENDING_VETH_INDEX")
                verify_veth_pair(host_idx, pilot_idx, item["namespace_inode"],
                                 peer_in_namespace=False)
                require(not run([IP, "netns", "pids", NS]).strip(), "ROLLBACK_NAMESPACE_IN_USE")
                run([IP, "link", "delete", H])
            elif kind == "namespace_pending":
                require(item["path_inode"] is not None and
                        NS_PATH.lstat().st_ino == item["path_inode"],
                        "ROLLBACK_PENDING_NAMESPACE_PATH")
                inode = namespace_inode()
                require(inode and not run([IP, "netns", "pids", NS]).strip() and
                        not any(row.get("ifname") == P for row in ns_json("link", "show")),
                        "ROLLBACK_PENDING_NAMESPACE_IDENTITY")
                run([IP, "netns", "delete", NS])
            elif kind == "file":
                _, path, inode, digest, uid, gid, mode = item
                if preserve_receipts and RECEIPTS in path.parents:
                    continue
                if path.parent == UNITS:
                    require(systemd_state(path.name)["ActiveState"] != "active", "ROLLBACK_ACTIVE_UNIT")
                info = strict_file(path, uid, gid, mode, digest)
                require(info.st_ino == inode, "ROLLBACK_FILE_IDENTITY")
                path.unlink()
                removed_units |= path.parent == UNITS
            elif kind == "dir":
                _, path, inode, uid, gid, mode = item
                if preserve_receipts and (path == RECEIPTS or path == RECEIPTS.parent):
                    continue
                info = path.lstat()
                require(stat.S_ISDIR(info.st_mode) and info.st_ino == inode and
                        (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (uid, gid, mode)
                        and not any(path.iterdir()), "ROLLBACK_DIR_IDENTITY")
                path.rmdir()
            elif kind == "docker_rule":
                _, handle, expected = item
                current = nft_json("list", "chain", "ip", "filter", "DOCKER-USER")
                matched = [row for row in nft_rules(current) if row.get("handle") == handle]
                require(len(matched) == 1 and normalize_nft({"rule": matched[0]}) == expected,
                        "ROLLBACK_NFT_RULE_IDENTITY")
                nft_run("delete", "rule", "ip", "filter", "DOCKER-USER", "handle", str(handle))
            elif kind == "nft_table":
                _, family, name, in_ns, handle = item
                current = nft_json("list", "table", family, name, namespace=in_ns)
                tables = [row["table"] for row in current["nftables"] if "table" in row]
                require(len(tables) == 1 and tables[0].get("handle") == handle, "ROLLBACK_NFT_TABLE_HANDLE")
                allowed = expected_filter if in_ns else expected_nat
                observed = nft_entries(current)
                require(not (Counter(canonical(row) for row in observed) -
                             Counter(canonical(row) for row in allowed)), "ROLLBACK_NFT_TABLE_DRIFT")
                nft_run("delete", "table", family, name, namespace=in_ns)
            elif kind == "veth":
                _, host_idx, pilot_idx, ns_inode = item
                try:
                    verify_veth_pair(host_idx, pilot_idx, ns_inode)
                except (Blocked, subprocess.TimeoutExpired):
                    verify_veth_pair(host_idx, pilot_idx, ns_inode, peer_in_namespace=False)
                require(not run([IP, "netns", "pids", NS]).strip(), "ROLLBACK_NAMESPACE_IN_USE")
                run([IP, "link", "delete", H])
            elif kind == "namespace":
                _, ns_inode, path_inode = item
                require(not namespace_dependency_failed, "ROLLBACK_NAMESPACE_DEPENDENCY")
                require(NS_PATH.lstat().st_ino == path_inode and namespace_inode() == ns_inode and
                        not run([IP, "netns", "pids", NS]).strip(),
                        "ROLLBACK_NAMESPACE_IDENTITY")
                require(not any(row.get("ifname") == P for row in ns_json("link", "show")),
                        "ROLLBACK_NAMESPACE_LINK")
                run([IP, "netns", "delete", NS])
            else:
                raise Blocked("ROLLBACK_UNKNOWN_KIND")
        except Exception:
            failures.append(kind)
            if kind in ("veth", "veth_pending") or (kind == "nft_table" and item[3] is True) or (
                    kind == "nft_table_pending" and item["namespace"] is True):
                namespace_dependency_failed = True
    if removed_units:
        try:
            run([SYSTEMCTL, "daemon-reload"])
        except (Blocked, OSError, subprocess.TimeoutExpired):
            failures.append("daemon_reload")
    return failures


def rollback_proven(preflight, profile, used_marker, grant_sha, *, preserve_receipts=False):
    """Require complete read-only evidence before reporting a completed rollback."""
    marker_data = (json.dumps({"grant_sha256": grant_sha,
                               "status": "CONSUMED_FOR_ONE_HOST_APPLICATION_ATTEMPT"},
                              sort_keys=True, separators=(",", ":")) + "\n").encode()
    strict_file(used_marker, 0, 0, 0o600, sha_bytes(marker_data))
    paths = (NS_PATH, MARKER, NETWORK_MARKER, RESOLVER_DIR, UNITS / NEW_SOCKET,
             UNITS / NEW_TEMPLATE, SOCKET_PATH)
    require(all(absent(path) for path in paths), "ROLLBACK_V2_PATH_REMAINS")
    if not preserve_receipts:
        require(absent(RECEIPTS.parent), "ROLLBACK_RECEIPT_PATH_REMAINS")
    else:
        require(not any(path.name.startswith(".tmp-") for path in RECEIPTS.iterdir()),
                "ROLLBACK_RECEIPT_TEMP_REMAINS")
    links = json_command([IP, "-j", "link", "show"])
    require(not any(row.get("ifname") in (H, P) for row in links), "ROLLBACK_V2_LINK_REMAINS")
    routes = route_arrays()
    require(routes == preflight["routes"], "ROLLBACK_HOST_ROUTE_DRIFT")
    collision_routes(routes)
    ruleset = nft_json("list", "ruleset")
    for row in ruleset["nftables"]:
        for kind in ("table", "chain", "rule", "set", "map"):
            value = row.get(kind, {})
            require(value.get("table") not in (NAT, FILTER) and
                    value.get("name") not in (NAT, FILTER) and
                    value.get("comment") != COMMENT, "ROLLBACK_V2_FIREWALL_REMAINS")
    docker = nft_json("list", "chain", "ip", "filter", "DOCKER-USER")
    require(nft_entries(docker, baseline=True) ==
            nft_entries(preflight["docker"], baseline=True), "ROLLBACK_DOCKER_DRIFT")
    require(stopped_units() == preflight["units"], "ROLLBACK_UNIT_DRIFT")
    require(historical_fingerprint(protected_paths(profile)) == preflight["historical"],
            "ROLLBACK_HISTORICAL_DRIFT")


def apply():
    profile, schema = verify_review()
    address, hostname = policy_tuple()
    socket, template = proposed_units()
    grant, grant_sha, used_marker = verify_grant()
    # Advisory process serialization uses the existing private directory; no file is created.
    lock_fd = os.open(BASE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    previous_signals = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    def interrupted(_number, _frame):
        raise Blocked("INTERRUPTED")
    for number in previous_signals:
        signal.signal(number, interrupted)
    created = []
    published = None
    receipt = None
    second = None
    try:
        first = host_snapshot(profile, address)
        second = host_snapshot(profile, address)  # Final read-only gate, immediately before mutation.
        require(first["routes"] == second["routes"] and
                nft_entries(first["docker"], baseline=True) == nft_entries(second["docker"], baseline=True)
                and first["historical"] == second["historical"] and first["units"] == second["units"],
                "PREFLIGHT_DRIFT")
        require(absent(used_marker), "GRANT_ALREADY_USED")
        one_shot_marker(used_marker, grant_sha, created)  # First mutation; retained on rollback.
        inode = create_namespace(created)
        links = ns_json("link", "show")
        require([row.get("ifname") for row in links] == ["lo"] and
                not any(route_arrays(namespace=True).values()), "NEW_NAMESPACE_NOT_EMPTY")
        ns_ip("link", "set", "lo", "up")
        namespace_forwarding_zero()
        create_dir(RESOLVER_DIR, 0, 989, 0o750, created)
        create_file(RESOLVER, f"{address} {hostname}\n".encode(), 0, 989, 0o640, created)
        require(sha(RESOLVER) == HOSTS_SHA, "RESOLVER_DIGEST")
        install_nft(address, created)
        host_idx, pilot_idx = create_veth(inode, created)
        run([IP, "link", "set", P, "netns", NS])
        verify_veth_pair(host_idx, pilot_idx, inode)
        run([IP, "address", "add", HOST_IP + "/30", "dev", H])
        run([IP, "link", "set", H, "up"])
        ns_ip("address", "add", PILOT_IP + "/30", "dev", P)
        ns_ip("link", "set", P, "up")
        ns_ip("route", "add", "default", "via", HOST_IP, "dev", P)
        ns_routes, host_routes = verify_routes(profile, schema, second["routes"])
        create_file(MARKER, (inode + "\n").encode(), 0, 0, 0o600, created)
        create_file(NETWORK_MARKER, (inode + "\n").encode(), 0, 0, 0o600, created)
        create_file(UNITS / NEW_SOCKET, socket, 0, 0, 0o644, created)
        create_file(UNITS / NEW_TEMPLATE, template, 0, 0, 0o644, created)
        run(["/usr/bin/systemd-analyze", "verify", str(UNITS / NEW_SOCKET), str(UNITS / NEW_TEMPLATE)])
        run([SYSTEMCTL, "daemon-reload"])
        stopped_units(allow_staged_v2=True)
        require(systemd_state(NEW_TEMPLATE)["UnitFileState"] == "static", "V2_TEMPLATE_STATE")
        require(sha(UNITS / OLD_SOCKET) == OLD_SOCKET_SHA and
                sha(UNITS / OLD_TEMPLATE) == OLD_TEMPLATE_SHA, "OLD_UNIT_CHANGED")
        nft_digest = verify_nft(address, second["docker"])
        namespace_forwarding_read()
        verify_staged_files(address, created)
        require(historical_fingerprint(protected_paths(profile)) == second["historical"],
                "HISTORICAL_ARTIFACT_CHANGED")
        receipt_dirs(created)
        receipt = build_receipt(grant, grant_sha, used_marker, profile, schema, created,
                                second, ns_routes, second["routes"], host_routes, nft_digest)
        published = publish_receipt(receipt)
        print(json.dumps({"status": "STAGED_UNQUALIFIED_PENDING_REVIEW", "receipt_sha256": published[1],
                          "receipt_name": published[0].name, "inspection_only": False,
                          "customer_activity": False}, sort_keys=True))
        return 0
    except BaseException as exc:
        if isinstance(exc, PublishedReceiptError):
            published = (exc.path, exc.digest)
        failures = rollback(created, address, preserve_receipts=published is not None)
        if second is not None and not absent(used_marker):
            try:
                rollback_proven(second, profile, used_marker, grant_sha,
                                preserve_receipts=published is not None)
            except BaseException:
                failures.append("cleanup_unverified")
        elif created:
            failures.append("cleanup_unverified")
        if published is not None and receipt is not None:
            correction = dict(receipt)
            correction["record_kind"] = "correction"
            correction["outcome"] = "ROLLBACK_INCOMPLETE" if failures else "ROLLED_BACK"
            correction["recorded_at_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            correction["correction_of"] = {
                "prior_filename": published[0].name, "prior_sha256": published[1],
                "prior_transaction_id": receipt["transaction_id"],
                "prior_outcome": receipt["outcome"], "reason_code": "POST_PUBLICATION_VERIFICATION_FAILED"}
            correction["rollback"] = {"status": "incomplete" if failures else "complete",
                                      "created_resource_identities_checked": not failures,
                                      "retained_v2_resource_names": failures}
            try:
                publish_receipt(correction)
            except Exception:
                failures.append("correction_receipt")
        print(json.dumps({"status": "ROLLBACK_INCOMPLETE" if failures else "ROLLED_BACK",
                          "cause": exc.args[0] if isinstance(exc, Blocked) else type(exc).__name__,
                          "retained_resource_categories": failures,
                          "grant_consumed": not absent(used_marker)}, sort_keys=True), file=sys.stderr)
        return 2
    finally:
        for number, previous in previous_signals.items():
            signal.signal(number, previous)
        os.close(lock_fd)


def check():
    profile, _schema = verify_review()
    address, _hostname = policy_tuple()
    proposed_units()
    host_snapshot(profile, address)
    print(json.dumps({"status": "READ_ONLY_PREFLIGHT_PASS", "session_id": SESSION,
                      "collision_clearance": False, "host_application_authorized": False,
                      "customer_activity_authorized": False}, sort_keys=True))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Guarded ORION v2 host staging; review-only until owner grant")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Read-only host preflight")
    mode.add_argument("--apply", action="store_true", help="One-shot v2 staging; requires separate root grant")
    args = parser.parse_args()
    try:
        return check() if args.check else apply()
    except BaseException as exc:
        print(json.dumps({"status": "BLOCKED", "reason":
                          exc.args[0] if isinstance(exc, Blocked) else type(exc).__name__},
                         sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
