"""In-memory rollback identity check; does not inspect or mutate host resources."""

import copy
import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name("apply-host-profile-v2.py")
SPEC = importlib.util.spec_from_file_location("orion_host_profile_v2_review_rollback", SCRIPT)
HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOST)


class RollbackIdentityTests(unittest.TestCase):
    def test_deletes_only_exact_owned_rule_handle(self):
        _nat, _filter, expected, _inbound = HOST.nft_expected("192.0.2.1")
        original_json, original_run = HOST.nft_json, HOST.nft_run
        deletes = []
        try:
            HOST.nft_run = lambda *args, namespace=False: deletes.append(args)
            drifted = copy.deepcopy(expected["rule"])
            drifted["handle"] = 17
            drifted["expr"] = []
            HOST.nft_json = lambda *args, namespace=False: {"nftables": [{"rule": drifted}]}
            self.assertEqual(HOST.rollback([("docker_rule", 17, expected)], "192.0.2.1"),
                             ["docker_rule"])
            self.assertEqual(deletes, [])
            owned = copy.deepcopy(expected["rule"])
            owned["handle"] = 17
            HOST.nft_json = lambda *args, namespace=False: {"nftables": [{"rule": owned}]}
            self.assertEqual(HOST.rollback([("docker_rule", 17, expected)], "192.0.2.1"), [])
            self.assertEqual(deletes, [("delete", "rule", "ip", "filter", "DOCKER-USER",
                                        "handle", "17")])
        finally:
            HOST.nft_json, HOST.nft_run = original_json, original_run


if __name__ == "__main__":
    unittest.main()
