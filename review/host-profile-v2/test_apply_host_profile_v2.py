"""Pure fixture tests for the review-only v2 host staging script."""

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name("apply-host-profile-v2.py")
SPEC = importlib.util.spec_from_file_location("orion_host_profile_v2_review", SCRIPT)
HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOST)


class ReviewOnlyHostScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile, cls.schema = HOST.verify_review()
        cls.synthetic = json.loads(
            (SCRIPT.parent / "V2_SYNTHETIC_OUTER_ROUTE_DELTA_REVIEW_20260926.json").read_text()
        )

    def test_bound_sources_and_unit_bytes(self):
        HOST.policy_tuple()
        socket, template = HOST.proposed_units()
        self.assertEqual(HOST.sha_bytes(socket), HOST.NEW_SOCKET_SHA)
        self.assertEqual(HOST.sha_bytes(template), HOST.NEW_TEMPLATE_SHA)
        self.assertEqual(HOST.sha(HOST.MANIFEST), HOST.MANIFEST_SHA)

    def test_approved_route_delta_exactly_matches_fixture_without_loopback(self):
        approved = self.schema["approved_host_delta_exact_objects"]
        self.assertEqual(approved, self.synthetic["link_and_address_delta_after_loopback_up"]["added"])
        self.assertEqual({key: len(value) for key, value in approved.items()},
                         {"ipv4_main": 1, "ipv4_all": 3, "ipv6_main": 1, "ipv6_all": 2})
        self.assertTrue(all(row["dev"] == HOST.H for rows in approved.values() for row in rows))

    def test_routes_accept_exact_arrays_and_reject_extra_or_modified_fields(self):
        original = HOST.route_arrays
        before = self.synthetic["outer_routes_after_link_move_before_addresses"]
        after = self.synthetic["outer_routes_after"]
        namespace = self.profile["expected_routes"]
        try:
            HOST.route_arrays = lambda namespace=False: (
                copy.deepcopy(namespace_routes) if namespace else copy.deepcopy(host_routes)
            )
            namespace_routes, host_routes = namespace, after
            HOST.verify_routes(self.profile, self.schema, before)
            host_routes = copy.deepcopy(after)
            host_routes["ipv6_all"][0]["scope"] = "link"
            with self.assertRaises(HOST.Blocked):
                HOST.verify_routes(self.profile, self.schema, before)
            host_routes = copy.deepcopy(after)
            host_routes["ipv4_main"].append(copy.deepcopy(after["ipv4_main"][0]))
            with self.assertRaises(HOST.Blocked):
                HOST.verify_routes(self.profile, self.schema, before)
            host_routes = copy.deepcopy(after)
            namespace_routes = copy.deepcopy(namespace)
            namespace_routes["ipv6_main"][0]["ifindex"] = 2
            with self.assertRaises(HOST.Blocked):
                HOST.verify_routes(self.profile, self.schema, before)
        finally:
            HOST.route_arrays = original

    def test_multiset_preserves_duplicates_and_missing_fields(self):
        row = self.schema["approved_host_delta_exact_objects"]["ipv4_main"][0]
        self.assertNotEqual(HOST.multiset_hash([row]), HOST.multiset_hash([row, row]))
        self.assertNotEqual(HOST.canonical(row), HOST.canonical({**row, "metric": 0}))
        with self.assertRaises(HOST.Blocked):
            HOST.parse_json('{"dst":"x","dst":"y"}')

    def test_firewall_expectations_are_bounded(self):
        nat, filt, outbound, inbound = HOST.nft_expected("192.0.2.1")
        self.assertEqual([len(nat), len(filt)], [3, 7])
        self.assertEqual(outbound["rule"]["comment"], HOST.COMMENT)
        self.assertEqual(inbound["rule"]["comment"], HOST.COMMENT)
        self.assertEqual(outbound["rule"]["expr"][-2]["match"]["right"], 443)

    def test_firewall_comparison_rejects_extra_rule(self):
        nat, filt, outbound, inbound = HOST.nft_expected("192.0.2.1")
        old = [{"chain": {"family": "ip", "table": "filter", "name": "DOCKER-USER", "handle": 1}},
               {"rule": {"family": "ip", "table": "filter", "chain": "DOCKER-USER",
                         "handle": 4, "expr": [{"counter": {"bytes": 12, "packets": 1}}]}}]
        def handles(rows):
            return [{kind: {**body, "handle": index + 1}} for index, row in enumerate(rows)
                    for kind, body in row.items()]
        values = [
            {"nftables": handles(nat)}, {"nftables": handles(filt)},
            {"nftables": [old[0], *handles([inbound, outbound]), old[1]]},
        ]
        original = HOST.nft_json
        try:
            HOST.nft_json = lambda *args, namespace=False: values.pop(0)
            self.assertEqual(len(HOST.verify_nft("192.0.2.1", {"nftables": old})), 64)
            values = [
                {"nftables": handles(nat)}, {"nftables": handles(filt)},
                {"nftables": [old[0], *handles([inbound, outbound]), old[1],
                              {"rule": {"family": "ip", "table": "filter",
                                        "chain": "DOCKER-USER", "handle": 9, "expr": []}}]},
            ]
            with self.assertRaises(HOST.Blocked):
                HOST.verify_nft("192.0.2.1", {"nftables": old})
        finally:
            HOST.nft_json = original

    def test_receipt_builder_validates_with_only_in_memory_observations(self):
        original = (HOST.namespace_inode, HOST.link_index, HOST.historical_fingerprint,
                    HOST.v2_file_meta)
        try:
            HOST.namespace_inode = lambda: "net:[123]"
            HOST.link_index = lambda name, namespace=False: 2 if namespace else 1
            HOST.historical_fingerprint = lambda paths: "a" * 64
            HOST.v2_file_meta = lambda created, marker: []
            result = HOST.build_receipt(
                {"grant_id": "a" * 32}, "b" * 64, Path("/unused"),
                self.profile, self.schema, [], {"historical": "a" * 64},
                self.profile["expected_routes"],
                self.synthetic["outer_routes_after_link_move_before_addresses"],
                self.synthetic["outer_routes_after"], "c" * 64,
            )
            self.assertEqual(result["evidence_class"], "PRODUCTION_RUNTIME_OBSERVATION")
            self.assertFalse(result["boundaries"]["customer_http_performed"])
            self.assertEqual(result["host_routes"]["ipv6_all"]["added_count"], 2)
        finally:
            (HOST.namespace_inode, HOST.link_index, HOST.historical_fingerprint,
             HOST.v2_file_meta) = original

    def test_check_dispatch_does_not_call_apply(self):
        original_snapshot, original_apply = HOST.host_snapshot, HOST.apply
        calls = []
        try:
            HOST.host_snapshot = lambda profile, address: calls.append("read_only_snapshot")
            HOST.apply = lambda: self.fail("apply path called")
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                result = HOST.check()
            self.assertEqual(result, 0)
            self.assertEqual(calls, ["read_only_snapshot"])
            self.assertFalse(json.loads(captured.getvalue())["host_application_authorized"])
        finally:
            HOST.host_snapshot, HOST.apply = original_snapshot, original_apply


if __name__ == "__main__":
    unittest.main()
