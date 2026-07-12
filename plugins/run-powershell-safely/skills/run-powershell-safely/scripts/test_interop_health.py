#!/usr/bin/env python3
from __future__ import annotations

import os
import unittest

import check_windows_interop as health


def good_static() -> dict:
    return {
        "wsl_interop_exists": True,
        "wsl_interop_is_socket": True,
        "binfmt_global_enabled": True,
        "binfmt_wslinterop_enabled": True,
        "init_executable": True,
        "windows_c_mount_present": True,
        "cmd_path": "/mnt/c/Windows/System32/cmd.exe",
        "powershell_path": "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "cmd_has_mz_magic": True,
        "powershell_has_mz_magic": True,
        "binfmt_wslinterop_present": True,
        "binfmt_wslinterop_disabled": False,
        "wsl_conf_explicitly_disables_interop": False,
    }


class StaticInteropHealthTests(unittest.TestCase):
    def test_healthy_requires_static_probes_and_clean_logs(self) -> None:
        classification, _action, external = health.classify(
            good_static(), {"ok": True, "error_kind": None}, {"ok": True, "error_kind": None}, {"available": True, "recent_count": 0}, 0
        )
        self.assertEqual(classification, "HEALTHY")
        self.assertFalse(external)

    def test_recent_vsock_warning_with_good_probes_is_transient_not_outage(self) -> None:
        classification, _action, external = health.classify(
            good_static(), {"ok": True, "error_kind": None}, {"ok": True, "error_kind": None}, {"available": True, "recent_count": 2}, 1
        )
        self.assertEqual(classification, "TRANSIENT_INTEROP_WARNING")
        self.assertFalse(external)

    def test_transport_failure_is_not_target_or_syntax(self) -> None:
        classification, _action, external = health.classify(
            good_static(), {"ok": False, "error_kind": "interop_transport"}, {"ok": False, "error_kind": "interop_transport"}, {"available": True, "recent_count": 1}, 1
        )
        self.assertEqual(classification, "BROKEN_TRANSPORT")
        self.assertTrue(external)

    def test_missing_binfmt_is_static_registration_failure(self) -> None:
        static = good_static()
        static["binfmt_wslinterop_enabled"] = False
        classification, _action, external = health.classify(
            static, None, None, {"available": True, "recent_count": 0}, 0
        )
        self.assertEqual(classification, "BROKEN_STATIC_REGISTRATION")
        self.assertTrue(external)

    def test_existing_disabled_handler_uses_only_narrow_conditional_repair(self) -> None:
        static = good_static()
        static["binfmt_wslinterop_enabled"] = False
        static["binfmt_wslinterop_disabled"] = True
        classification, action, external = health.classify(
            static, None, None, {"available": True, "recent_count": 0}, 0
        )
        self.assertEqual(classification, "EXISTING_HANDLER_DISABLED")
        self.assertIn("existing WSLInterop entry", action)
        self.assertFalse(external)

    def test_explicit_config_disable_belongs_to_config_owner(self) -> None:
        static = good_static()
        static["wsl_conf_explicitly_disables_interop"] = True
        classification, _action, external = health.classify(
            static, None, None, {"available": True, "recent_count": 0}, 0
        )
        self.assertEqual(classification, "INTEROP_DISABLED_BY_CONFIG")
        self.assertTrue(external)

    def test_invalid_pe_target_does_not_blame_wsl_registration(self) -> None:
        static = good_static()
        static["powershell_has_mz_magic"] = False
        classification, action, external = health.classify(
            static, None, None, {"available": True, "recent_count": 0}, 0
        )
        self.assertEqual(classification, "TARGET_BINARY_INVALID")
        self.assertIn("not a valid PE", action)
        self.assertTrue(external)


@unittest.skipUnless(os.environ.get("WINDOWS_INTEROP_HEALTH_INTEGRATION") == "1", "set WINDOWS_INTEROP_HEALTH_INTEGRATION=1")
class LiveInteropHealthTests(unittest.TestCase):
    def test_live_health_is_truthful_and_usable(self) -> None:
        expect_host = os.environ.get("WINDOWS_EXPECT_HOST", "").strip()
        if not expect_host:
            self.fail(
                "WINDOWS_EXPECT_HOST must be set when WINDOWS_INTEROP_HEALTH_INTEGRATION=1"
            )
        report = health.build_report(
            expect_host, 8.0, 180.0, True
        )
        self.assertIn(
            report["classification"],
            {"HEALTHY", "TRANSIENT_INTEROP_WARNING", "USABLE_LOGS_UNVERIFIED"},
        )
        self.assertTrue(report["usable"], report)
        self.assertTrue(report["cmd_probe"]["ok"], report)
        self.assertTrue(report["powershell_probe"]["ok"], report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
