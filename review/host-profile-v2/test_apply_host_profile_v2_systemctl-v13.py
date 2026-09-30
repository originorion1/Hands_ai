"""Mock-only diagnostics for each systemctl read reached by --check."""

import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("apply-host-profile-v2.py")
SPEC = importlib.util.spec_from_file_location("orion_host_profile_v2_systemctl", SCRIPT)
HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOST)


def show(unit):
    return (HOST.SYSTEMCTL, "show", "--no-pager", unit,
            "--property=LoadState,ActiveState,UnitFileState,SubState")


def list_files(prefix):
    return (HOST.SYSTEMCTL, "list-unit-files", "--no-legend", "--no-pager",
            prefix + "*.service")


def list_units(prefix):
    return (HOST.SYSTEMCTL, "list-units", "--all", "--plain", "--no-legend",
            "--no-pager", "--full", prefix + "*.service")


OLD = "orion-pilot-metadata@"
V2 = "orion-pilot-metadata-v2@"
OLD_INSTANCE = OLD + "1.service"
V2_INSTANCE = V2 + "1.service"
OPERATIONS = {
    "SHOW_OLD_SOCKET": show(HOST.OLD_SOCKET),
    "SHOW_V2_SOCKET": show(HOST.NEW_SOCKET),
    "SHOW_PLAIN_SERVICE": show("orion-pilot-metadata.service"),
    "LIST_OLD_INSTANCE_FILES": list_files(OLD),
    "LIST_OLD_INSTANCES": list_units(OLD),
    "SHOW_OLD_INSTANCE": show(OLD_INSTANCE),
    "LIST_V2_INSTANCE_FILES": list_files(V2),
    "LIST_V2_INSTANCES": list_units(V2),
    "SHOW_V2_INSTANCE": show(V2_INSTANCE),
}


class SystemctlDiagnosticsTests(unittest.TestCase):
    def test_each_failure_has_fixed_label_and_return_code_without_output(self):
        self.assertEqual(set(OPERATIONS), HOST.SYSTEMCTL_CHECK_LABELS)
        for failed_label, failed_argv in OPERATIONS.items():
            with self.subTest(failed_label=failed_label):
                calls = []

                def fake_run(argv, **kwargs):
                    argv = tuple(argv)
                    calls.append(argv)
                    self.assertIn(argv, OPERATIONS.values())
                    self.assertTrue(kwargs["capture_output"])
                    if argv == failed_argv:
                        return subprocess.CompletedProcess(argv, 17,
                            "SECRET_UNIT_OUTPUT\n", "SECRET_SYSTEMD_ERROR\n")
                    if argv == list_units(OLD):
                        output = OLD_INSTANCE + " loaded inactive dead\n"
                    elif argv == list_units(V2):
                        output = V2_INSTANCE + " loaded inactive dead\n"
                    elif argv[1] == "show":
                        output = ("LoadState=loaded\nActiveState=inactive\n"
                                  "UnitFileState=disabled\nSubState=dead\n")
                    else:
                        output = ""
                    return subprocess.CompletedProcess(argv, 0, output, "")

                stdout, stderr = io.StringIO(), io.StringIO()
                with mock.patch.object(HOST.subprocess, "run", side_effect=fake_run), \
                     mock.patch.object(HOST, "absent", return_value=True), \
                     contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    with self.assertRaises(HOST.Blocked) as raised:
                        HOST.stopped_units()
                self.assertEqual(str(raised.exception), f"SYSTEMCTL_{failed_label}_RC_17")
                self.assertIn(failed_argv, calls)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")
                self.assertNotIn("SECRET", str(raised.exception))

    def test_success_uses_read_output_without_printing_it(self):
        def fake_run(argv, **kwargs):
            argv = tuple(argv)
            self.assertIn(argv, OPERATIONS.values())
            self.assertTrue(kwargs["capture_output"])
            if argv == list_units(OLD):
                output = OLD_INSTANCE + " loaded inactive dead\n"
            elif argv == list_units(V2):
                output = V2_INSTANCE + " loaded inactive dead\n"
            elif argv[1] == "show":
                output = "LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\nSubState=dead\n"
            else:
                output = ""
            return subprocess.CompletedProcess(argv, 0, output, "SECRET_IGNORED_STDERR")

        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(HOST.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(HOST, "absent", return_value=True), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = HOST.stopped_units()
        self.assertTrue(result["old_socket_stopped_disabled"])
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
