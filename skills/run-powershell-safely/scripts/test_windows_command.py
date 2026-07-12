#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import errno
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
RUNNER_PATH = HERE / "windows_command.py"
CLASSIFIER_PATH = HERE / "classify-windows-command-failure.sh"
SPEC = importlib.util.spec_from_file_location("windows_command", RUNNER_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class StaticRunnerTests(unittest.TestCase):
    def test_literal_dollar_is_not_forbidden(self) -> None:
        runner.safety_check("$p = 'literal $ value'\n$p", "read")

    def test_forbidden_transports_and_bypasses(self) -> None:
        bad = [
            "powershell.exe -EncodedCommand abc",
            "powershell.exe -ExecutionPolicy Bypass -File task.ps1",
            "Invoke-Expression $text",
            "Set-MpPreference -DisableRealtimeMonitoring $true",
        ]
        for source in bad:
            with self.subTest(source=source), self.assertRaises(runner.RunnerUsageError):
                runner.safety_check(source, "read")

    def test_read_intent_rejects_write_cmdlets(self) -> None:
        with self.assertRaises(runner.RunnerUsageError):
            runner.safety_check("Remove-Item -LiteralPath 'C:\\exact.txt'", "read")

    def test_read_intent_rejects_common_protected_mutations(self) -> None:
        bad = [
            "Stop-Process -Id 123",
            "Register-ScheduledTask -TaskName 'x' -Action $action",
            "Disable-NetAdapter -Name 'Ethernet'",
            "Remove-AppxPackage -Package 'x'",
            "Set-Acl -LiteralPath 'C:\\x' -AclObject $acl",
            "schtasks.exe /delete /tn x /f",
            "reg.exe delete HKCU\\Software\\x /f",
        ]
        for source in bad:
            with self.subTest(source=source), self.assertRaises(runner.RunnerUsageError):
                runner.safety_check(source, "read")

    def test_wildcard_delete_is_always_rejected(self) -> None:
        with self.assertRaises(runner.RunnerUsageError):
            runner.safety_check("Remove-Item -Path 'C:\\Temp\\*'", "write")

    def test_payload_exit_is_rejected_to_preserve_envelope(self) -> None:
        with self.assertRaises(runner.RunnerUsageError):
            runner.safety_check("exit 0", "read")

    def test_task_source_never_enters_process_argv(self) -> None:
        args = runner.build_parser().parse_args(["powershell", "--dry-run"])
        execution = runner.execute(args, "$value = 'secret task source'")
        self.assertFalse(execution.result["argv_has_task_source"])

    def test_cmd_exit_b_is_allowed_for_batch_status(self) -> None:
        runner.safety_check("@echo off\r\nexit /b 19\r\n", "read")

    def test_marker_parser(self) -> None:
        payload = {"schema": runner.SCHEMA, "ok": True}
        clean, parsed = runner.extract_result(
            "warning\n" + runner.MARKER + json.dumps(payload) + "\n"
        )
        self.assertEqual(clean, "warning\n")
        self.assertEqual(parsed, payload)

    def test_transport_failures_are_not_labeled_as_powershell_syntax(self) -> None:
        vsock = runner.transport_result(
            "powershell", "UtilAcceptVsockAnyPort: accept4 failed 110", 1
        )
        dispatch = runner.transport_result("powershell", "Exec format error", 126)
        self.assertEqual(vsock["error_kind"], "interop_transport")
        self.assertEqual(dispatch["error_kind"], "interop_binary_dispatch")

        launch_dispatch = runner.launch_exception_result(
            "powershell", OSError(errno.ENOEXEC, "Exec format error")
        )
        launch_transport = runner.launch_exception_result(
            "powershell", OSError(errno.ETIMEDOUT, "interop timed out")
        )
        self.assertEqual(launch_dispatch["error_kind"], "interop_binary_dispatch")
        self.assertEqual(launch_transport["error_kind"], "interop_transport")

    def test_failure_classifier_separates_interop_and_parser_layers(self) -> None:
        cases = {
            "UtilAcceptVsockAnyPort: accept4 failed 110": "classification=interop_transport",
            "cannot execute binary file: Exec format error": "classification=interop_binary_dispatch",
            "ParseException: An empty pipe element is not allowed": "classification=powershell_parse",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                proc = subprocess.run(
                    ["bash", str(CLASSIFIER_PATH)],
                    input=source,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                self.assertIn(expected, proc.stdout)

    def test_write_requires_host_and_receipt(self) -> None:
        args = runner.build_parser().parse_args(["powershell", "--intent", "write", "--dry-run"])
        with self.assertRaises(runner.RunnerUsageError):
            runner.execute(args, "Set-Content -LiteralPath 'C:\\x' -Value 'x'")


@unittest.skipUnless(os.environ.get("WINDOWS_RUNNER_INTEGRATION") == "1", "set WINDOWS_RUNNER_INTEGRATION=1")
class WindowsIntegrationTests(unittest.TestCase):
    expect_host = os.environ.get("WINDOWS_EXPECT_HOST", "").strip()

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if not cls.expect_host:
            raise RuntimeError(
                "WINDOWS_EXPECT_HOST must be set when WINDOWS_RUNNER_INTEGRATION=1"
            )

    def run_case(
        self,
        source: str,
        mode: str = "powershell",
        *,
        extra_args: list[str] | None = None,
        expect_host: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], str, dict]:
        command = [
            sys.executable,
            str(RUNNER_PATH),
            mode,
            "--intent",
            "read",
            "--expect-host",
            expect_host or self.expect_host,
        ]
        command.extend(extra_args or [])
        proc = subprocess.run(
            command,
            input=source,
            text=True,
            capture_output=True,
            timeout=40,
        )
        clean_stderr, result = runner.extract_result(proc.stderr)
        if result is None:
            self.fail(f"runner did not emit a result marker: stdout={proc.stdout!r} stderr={proc.stderr!r}")
        return proc, clean_stderr, result

    def test_literal_dollar_quotes_unicode_json_and_spaces(self) -> None:
        source = """
$value = 'literal $() and café — Indonesia 🚀'
[pscustomobject]@{ Value = $value; Path = 'C:\\Program Files' } | ConvertTo-Json -Compress
"""
        proc, clean_stderr, result = self.run_case(source)
        self.assertEqual(proc.returncode, 0, clean_stderr)
        self.assertTrue(result["ok"])
        actual = json.loads(proc.stdout)
        self.assertEqual(actual["Value"], "literal $() and café — Indonesia 🚀")
        self.assertEqual(actual["Path"], "C:\\Program Files")

    def test_parser_error_is_nonzero_before_output(self) -> None:
        source = "Write-Output 'must-not-run'\nforeach ($x in 1) { $x } | Out-String\n"
        proc, _clean_stderr, result = self.run_case(source)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(result["error_kind"], "parse")
        self.assertNotIn("must-not-run", proc.stdout)

    def test_bash_backslash_continuation_is_rejected_before_output(self) -> None:
        source = "Write-Output 'must-not-run'\nGet-Process \\\n  | Select-Object -First 1\n"
        proc, _clean_stderr, result = self.run_case(source)
        self.assertEqual(proc.returncode, 65)
        self.assertEqual(result["error_kind"], "parse")
        self.assertNotIn("must-not-run", proc.stdout)

    def test_powershell_exception_is_nonzero(self) -> None:
        proc, _clean_stderr, result = self.run_case("throw 'expected-integration-error'\n")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(result["error_kind"], "powershell_exception")

    def test_native_exit_is_preserved(self) -> None:
        proc, _clean_stderr, result = self.run_case("cmd.exe /d /c exit 23\n")
        self.assertEqual(proc.returncode, 23)
        self.assertEqual(result["native_exit_code"], 23)

    def test_foreach_collection_pattern(self) -> None:
        source = """
$rows = foreach ($x in 1,2,3) { [pscustomobject]@{ Value = $x } }
$rows | ConvertTo-Json -Compress
"""
        proc, clean_stderr, result = self.run_case(source)
        self.assertEqual(proc.returncode, 0, clean_stderr)
        self.assertTrue(result["ok"])
        rows = json.loads(proc.stdout)
        self.assertEqual([row["Value"] for row in rows], [1, 2, 3])

    def test_default_windows_cwd_is_not_wsl_unc_for_cmd(self) -> None:
        proc, clean_stderr, result = self.run_case("cmd.exe /d /c cd\n")
        self.assertEqual(proc.returncode, 0, clean_stderr)
        self.assertNotIn("wsl.localhost", proc.stdout.lower())
        self.assertTrue(result["ok"])

    def test_cmd_mode_preserves_batch_syntax(self) -> None:
        source = "@echo off\r\nset VALUE=hello\r\necho %VALUE% ^& safe\r\nexit /b 0\r\n"
        proc, clean_stderr, result = self.run_case(source, mode="cmd")
        self.assertEqual(proc.returncode, 0, clean_stderr)
        self.assertTrue(result["ok"])
        self.assertEqual(result["cleanup_status"], "removed")
        self.assertEqual(proc.stdout.strip(), "hello & safe")

    def test_json_input_keeps_types_and_shape(self) -> None:
        data = {"items": [{"enabled": True, "note": None}], "label": "café 🚀"}
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json") as handle:
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            source = """
$data = $CodexInputData
[pscustomobject]@{
  Count = @($data.items).Count
  Enabled = [bool]$data.items[0].enabled
  NoteIsNull = ($null -eq $data.items[0].note)
  Label = [string]$data.label
} | ConvertTo-Json -Compress
"""
            proc, clean_stderr, result = self.run_case(
                source, extra_args=["--input-json", handle.name]
            )
        self.assertEqual(proc.returncode, 0, clean_stderr)
        self.assertTrue(result["ok"])
        actual = json.loads(proc.stdout)
        self.assertEqual(actual, {"Count": 1, "Enabled": True, "NoteIsNull": True, "Label": "café 🚀"})

    def test_explicit_windows_working_directory(self) -> None:
        proc, clean_stderr, result = self.run_case(
            "(Get-Location).Path\n", extra_args=["--cwd", "C:\\Windows"]
        )
        self.assertEqual(proc.returncode, 0, clean_stderr)
        self.assertTrue(result["ok"])
        self.assertEqual(proc.stdout.strip().lower(), "c:\\windows")

    def test_get_command_discovery(self) -> None:
        proc, clean_stderr, result = self.run_case(
            "(Get-Command -Name Get-CimInstance -CommandType Cmdlet).Name\n"
        )
        self.assertEqual(proc.returncode, 0, clean_stderr)
        self.assertTrue(result["ok"])
        self.assertEqual(proc.stdout.strip(), "Get-CimInstance")

    def test_warning_stream_cannot_corrupt_result_marker(self) -> None:
        proc, clean_stderr, result = self.run_case(
            "Write-Warning 'expected warning'\nWrite-Output 'expected output'\n"
        )
        self.assertEqual(proc.returncode, 0, clean_stderr)
        self.assertTrue(result["ok"])
        self.assertIn("expected warning", proc.stdout)
        self.assertIn("expected output", proc.stdout)

    def test_host_mismatch_stops_before_payload(self) -> None:
        proc, _clean_stderr, result = self.run_case(
            "Write-Output 'must-not-run'\n", expect_host="DELIBERATELY-WRONG-HOST"
        )
        self.assertEqual(proc.returncode, 66)
        self.assertEqual(result["error_kind"], "host_mismatch")
        self.assertNotIn("must-not-run", proc.stdout)

    def test_timeout_is_distinct_from_parser_failure(self) -> None:
        proc, _clean_stderr, result = self.run_case(
            "Start-Sleep -Seconds 3\n", extra_args=["--timeout", "0.2"]
        )
        self.assertEqual(proc.returncode, 124)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["error_kind"], "timeout")

    def test_cmd_mode_preserves_distinctive_exit_code(self) -> None:
        proc, _clean_stderr, result = self.run_case("@echo off\r\nexit /b 19\r\n", mode="cmd")
        self.assertEqual(proc.returncode, 19)
        self.assertEqual(result["native_exit_code"], 19)
        self.assertEqual(result["cleanup_status"], "removed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
