"""Fail-closed parsing of namespace-relative RTM_GETNSID replies."""

import importlib.util
import struct
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location("orion_test_veth_nsid", Path(__file__).with_name("veth_nsid.py"))
NSID = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NSID)


class FakeSocket:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def settimeout(self, _seconds):
        return None

    def bind(self, _address):
        return None

    def getsockname(self):
        return (123, 0)

    def send(self, data):
        self.request = data

    def recvmsg(self, _size):
        return self.response, [], 0, (0, 0)


def reply(*ids, kind=88):
    attrs = b"".join(struct.pack("=HHi", 8, 1, value) for value in ids)
    return struct.pack("=IHHII", 20 + len(attrs), kind, 0, 1, 123) + b"\0" * 4 + attrs


class NsidReplyTests(unittest.TestCase):
    def test_pinned_fd_request_accepts_one_nonnegative_mapping(self):
        fake = FakeSocket(reply(7))
        with mock.patch.object(NSID.socket, "socket", return_value=fake):
            self.assertEqual(NSID._query_nsid(42), 7)
        self.assertEqual(fake.request[16:20], b"\0" * 4)
        self.assertEqual(fake.request[20:28], struct.pack("=HHI", 8, 3, 42))

    def test_missing_ambiguous_or_unassigned_mapping_fails_closed(self):
        for label, response in (("missing", reply()), ("ambiguous", reply(0, 1)),
                                ("unassigned", reply(-1)), ("error", reply(0, kind=2))):
            with self.subTest(label=label), \
                 mock.patch.object(NSID.socket, "socket", return_value=FakeSocket(response)), \
                 self.assertRaises(NSID.NsidError):
                NSID._query_nsid(42)

    def test_truncated_reply_fails_closed(self):
        response = reply(0)[:-1]
        with mock.patch.object(NSID.socket, "socket", return_value=FakeSocket(response)), \
             self.assertRaises(NSID.NsidError):
            NSID._query_nsid(42)

    def test_unexpected_child_exception_exits_without_running_parent_continuation(self):
        class ChildExit(BaseException):
            pass

        for failure in (struct.error("bad netlink payload"), RuntimeError("unexpected")):
            with self.subTest(failure=type(failure).__name__), \
                 mock.patch.object(NSID.os, "pipe", return_value=(10, 11)), \
                 mock.patch.object(NSID.os, "fork", return_value=0), \
                 mock.patch.object(NSID.os, "close"), \
                 mock.patch.object(NSID.os, "setns"), \
                 mock.patch.object(NSID, "_query_nsid", side_effect=failure), \
                 mock.patch.object(NSID.os, "write") as write, \
                 mock.patch.object(NSID.os, "_exit", side_effect=ChildExit) as exit_child:
                with self.assertRaises(ChildExit):
                    NSID._query_from_pinned_namespace(20, 21)
            exit_child.assert_called_once_with(1)
            write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
