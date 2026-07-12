#!/usr/bin/env python3
"""Deterministic WSL-to-Windows PowerShell/cmd runner.

Task source travels on stdin. Only a fixed, runner-owned bootstrap is placed in
the Windows process argv. The bootstrap compiles the complete task before it
executes anything and emits a machine-readable result marker on stderr.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MARKER = "__CODEX_WINDOWS_RESULT_V1__"
SCHEMA = "codex.windows.result.v1"

POWERSHELL_BOOTSTRAP = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$result = [ordered]@{
  schema = 'codex.windows.result.v1'; ok = $false; mode = 'powershell';
  target = 'local'; hostname = [Environment]::MachineName;
  username = [Environment]::UserName; powershell_version = $PSVersionTable.PSVersion.ToString();
  process_exit_code = 1; native_exit_code = $null; timed_out = $false;
  duration_ms = 0; error_kind = $null; error_id = $null; message = $null;
  intent = $env:CODEX_WINDOWS_INTENT; cleanup_status = 'not_needed'
}
function Emit-CodexResult {
  $result.duration_ms = [int64]$sw.ElapsedMilliseconds
  $json = $result | ConvertTo-Json -Compress -Depth 6
  [Console]::Error.WriteLine('__CODEX_WINDOWS_RESULT_V1__' + $json)
}
try {
  if ($env:CODEX_WINDOWS_EXPECT_HOST -and
      -not [string]::Equals($result.hostname, $env:CODEX_WINDOWS_EXPECT_HOST, [StringComparison]::OrdinalIgnoreCase)) {
    $result.error_kind = 'host_mismatch'
    $result.message = 'Expected host does not match the actual Windows host.'
    $result.process_exit_code = 66
    Emit-CodexResult
    exit 66
  }
  if ($env:CODEX_WINDOWS_EXPECT_USER -and
      -not [string]::Equals($result.username, $env:CODEX_WINDOWS_EXPECT_USER, [StringComparison]::OrdinalIgnoreCase)) {
    $result.error_kind = 'user_mismatch'
    $result.message = 'Expected user does not match the actual Windows user.'
    $result.process_exit_code = 67
    Emit-CodexResult
    exit 67
  }
  if ($env:CODEX_WINDOWS_CWD) {
    Set-Location -LiteralPath $env:CODEX_WINDOWS_CWD
  } elseif ((Get-Location).ProviderPath -like '\\*') {
    Set-Location -LiteralPath $env:USERPROFILE
  }
  # WHY: Windows PowerShell 5.1's Get-Content default can decode UTF-8 JSON as
  # the legacy ANSI code page. Decode explicitly before task code can consume it.
  $CodexInputData = $null
  if ($env:CODEX_WINDOWS_INPUT_PATH) {
    $jsonText = [IO.File]::ReadAllText($env:CODEX_WINDOWS_INPUT_PATH, [Text.Encoding]::UTF8)
    $CodexInputData = $jsonText | ConvertFrom-Json
  }
  $source = [Console]::In.ReadToEnd()
  try {
    $block = [ScriptBlock]::Create($source)
  } catch [System.Management.Automation.ParseException] {
    $result.error_kind = 'parse'
    $result.error_id = $_.FullyQualifiedErrorId
    $result.message = $_.Exception.Message
    $result.process_exit_code = 65
    Emit-CodexResult
    exit 65
  }
  $global:LASTEXITCODE = 0
  & $block
  $result.native_exit_code = [int]$global:LASTEXITCODE
  if ($result.native_exit_code -ne 0) {
    $result.error_kind = 'native_exit'
    $result.message = 'A native Windows command returned a nonzero exit code.'
    $result.process_exit_code = $result.native_exit_code
    Emit-CodexResult
    exit $result.process_exit_code
  }
  $result.ok = $true
  $result.process_exit_code = 0
  Emit-CodexResult
  exit 0
} catch {
  $result.error_kind = 'powershell_exception'
  $result.error_id = $_.FullyQualifiedErrorId
  $result.message = $_.Exception.Message
  $result.process_exit_code = 1
  Emit-CodexResult
  exit 1
}
""".strip()

CMD_BOOTSTRAP = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$result = [ordered]@{
  schema = 'codex.windows.result.v1'; ok = $false; mode = 'cmd';
  target = 'local'; hostname = [Environment]::MachineName;
  username = [Environment]::UserName; powershell_version = $PSVersionTable.PSVersion.ToString();
  process_exit_code = 1; native_exit_code = $null; timed_out = $false;
  duration_ms = 0; error_kind = $null; error_id = $null; message = $null;
  intent = $env:CODEX_WINDOWS_INTENT; cleanup_status = 'not_started'
}
function Emit-CodexResult {
  $result.duration_ms = [int64]$sw.ElapsedMilliseconds
  $json = $result | ConvertTo-Json -Compress -Depth 6
  [Console]::Error.WriteLine('__CODEX_WINDOWS_RESULT_V1__' + $json)
}
$batchPath = $null
try {
  if ($env:CODEX_WINDOWS_EXPECT_HOST -and
      -not [string]::Equals($result.hostname, $env:CODEX_WINDOWS_EXPECT_HOST, [StringComparison]::OrdinalIgnoreCase)) {
    throw [InvalidOperationException]::new('HOST_MISMATCH')
  }
  if ($env:CODEX_WINDOWS_EXPECT_USER -and
      -not [string]::Equals($result.username, $env:CODEX_WINDOWS_EXPECT_USER, [StringComparison]::OrdinalIgnoreCase)) {
    throw [InvalidOperationException]::new('USER_MISMATCH')
  }
  if ($env:CODEX_WINDOWS_CWD) {
    Set-Location -LiteralPath $env:CODEX_WINDOWS_CWD
  } elseif ((Get-Location).ProviderPath -like '\\*') {
    Set-Location -LiteralPath $env:USERPROFILE
  }
  $source = [Console]::In.ReadToEnd()
  $batchPath = Join-Path $env:TEMP ('codex-cmd-' + $env:CODEX_WINDOWS_RUN_ID + '.cmd')
  [IO.File]::WriteAllText($batchPath, $source, [Text.Encoding]::Default)
  & $env:ComSpec /d /q /c $batchPath
  $result.native_exit_code = [int]$global:LASTEXITCODE
  $result.process_exit_code = $result.native_exit_code
  if ($result.native_exit_code -eq 0) {
    $result.ok = $true
  } else {
    $result.error_kind = 'native_exit'
    $result.message = 'The cmd batch returned a nonzero exit code.'
  }
} catch {
  if ($_.Exception.Message -eq 'HOST_MISMATCH') {
    $result.error_kind = 'host_mismatch'; $result.process_exit_code = 66
  } elseif ($_.Exception.Message -eq 'USER_MISMATCH') {
    $result.error_kind = 'user_mismatch'; $result.process_exit_code = 67
  } else {
    $result.error_kind = 'powershell_exception'; $result.process_exit_code = 1
  }
  $result.error_id = $_.FullyQualifiedErrorId
  $result.message = $_.Exception.Message
} finally {
  if ($batchPath -and (Test-Path -LiteralPath $batchPath)) {
    try { Remove-Item -LiteralPath $batchPath -Force -ErrorAction Stop; $result.cleanup_status = 'removed' }
    catch { $result.cleanup_status = 'failed_exact_path'; if (-not $result.error_kind) { $result.error_kind = 'cleanup' } }
  } else {
    $result.cleanup_status = 'not_created_or_already_absent'
  }
}
Emit-CodexResult
exit $result.process_exit_code
""".strip()


FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("opaque encoded command transport", re.compile(r"(?i)(?:^|\s)-EncodedCommand\b")),
    ("execution-policy bypass", re.compile(r"(?i)\bExecutionPolicy\s+Bypass\b")),
    ("dynamic expression evaluation", re.compile(r"(?i)\b(?:Invoke-Expression|iex)\b")),
    (
        "antivirus preference change",
        re.compile(r"(?i)\b(?:Set|Add|Remove)-MpPreference\b"),
    ),
    ("execution-policy change", re.compile(r"(?i)\bSet-ExecutionPolicy\b")),
    (
        "download-and-run pipeline",
        re.compile(r"(?is)\b(?:Invoke-WebRequest|iwr|curl(?:\.exe)?)\b.*\|\s*(?:Invoke-Expression|iex)\b"),
    ),
    # WHY: PowerShell `exit` can terminate the host before the result envelope;
    # cmd's scoped `exit /b` is safe and must remain available for batch status.
    ("payload exit statement", re.compile(r"(?im)^\s*exit(?!\s*/b)(?:\s|$)")),
)

WRITE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\b(?:Remove-Item|Set-Content|Add-Content|Clear-Content|New-Item|Move-Item|Copy-Item|Rename-Item|"
        r"Set-Item|Set-ItemProperty|New-ItemProperty|Remove-ItemProperty|Start-Service|Stop-Service|Restart-Service|"
        r"Set-Service|New-NetFirewallRule|Set-NetFirewallRule|Remove-NetFirewallRule|Clear-Disk|Initialize-Disk|"
        r"New-Partition|Format-Volume|Enable-WindowsOptionalFeature|Disable-WindowsOptionalFeature)\b"
    ),
    # WHY: read intent is an advisory preflight guard, not a sandbox. Cover
    # common Windows owner cmdlets so an accidental process/account/task/
    # network/disk mutation is stopped before PowerShell receives the source.
    re.compile(
        r"(?i)\b(?:Start-Process|Stop-Process|Register-ScheduledTask|Unregister-ScheduledTask|"
        r"Set-ScheduledTask|Start-ScheduledTask|Stop-ScheduledTask|Enable-ScheduledTask|Disable-ScheduledTask|"
        r"New-LocalUser|Set-LocalUser|Remove-LocalUser|Rename-LocalUser|Enable-LocalUser|Disable-LocalUser|"
        r"New-LocalGroup|Set-LocalGroup|Remove-LocalGroup|Rename-LocalGroup|Add-LocalGroupMember|"
        r"Remove-LocalGroupMember|Set-Acl|Enable-NetAdapter|Disable-NetAdapter|Restart-NetAdapter|"
        r"Rename-NetAdapter|Set-NetAdapter|New-NetIPAddress|Set-NetIPAddress|Remove-NetIPAddress|"
        r"New-NetRoute|Set-NetRoute|Remove-NetRoute|Set-DnsClientServerAddress|Set-NetFirewallProfile|"
        r"Enable-NetFirewallRule|Disable-NetFirewallRule|Set-NetConnectionProfile|Install-Package|"
        r"Uninstall-Package|Add-AppxPackage|Remove-AppxPackage|Add-AppxProvisionedPackage|"
        r"Remove-AppxProvisionedPackage|Restart-Computer|Stop-Computer|Rename-Computer|Set-TimeZone|"
        r"Set-Date|Add-Printer|Set-Printer|Remove-Printer|New-SmbShare|Set-SmbShare|Remove-SmbShare|"
        r"Compress-Archive|Expand-Archive)\b"
    ),
    re.compile(r"(?i)\bwinget(?:\.exe)?\s+(?:install|uninstall|upgrade)\b"),
    re.compile(r"(?i)\bmsiexec(?:\.exe)?\b"),
    re.compile(
        r"(?im)^\s*(?:del|erase|rd|rmdir|move|copy|ren|taskkill(?:\.exe)?|shutdown(?:\.exe)?|"
        r"format(?:\.com)?|diskpart(?:\.exe)?|bcdedit(?:\.exe)?|takeown(?:\.exe)?|icacls(?:\.exe)?|"
        r"reg(?:\.exe)?\s+(?:add|delete|copy|import|load|restore|save|unload)|"
        r"sc(?:\.exe)?\s+(?:config|create|delete|start|stop|pause|continue)|"
        r"schtasks(?:\.exe)?\s+/(?:create|change|delete|end|run)|"
        r"net(?:\.exe)?\s+(?:user|localgroup|share)\b)"
    ),
)

WILDCARD_DELETE = re.compile(
    r"(?im)(?:\bRemove-Item\b[^\r\n]*[-'\"\w:/.\\]*[*?]|^\s*(?:del|erase|rd|rmdir)\b[^\r\n]*[*?])"
)


class RunnerUsageError(ValueError):
    pass


@dataclass
class Execution:
    returncode: int
    stdout: str
    stderr: str
    result: dict[str, object]


def read_source(name: str) -> str:
    if name == "-":
        return sys.stdin.read()
    return Path(name).read_text(encoding="utf-8-sig")


def safety_check(source: str, intent: str) -> None:
    if not source.strip():
        raise RunnerUsageError("task source is empty")
    for label, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(source):
            raise RunnerUsageError(f"refusing {label}; use a transparent, reviewable mechanism")
    if WILDCARD_DELETE.search(source):
        raise RunnerUsageError("refusing wildcard deletion; use an exact literal target")
    if intent == "read" and any(pattern.search(source) for pattern in WRITE_PATTERNS):
        raise RunnerUsageError("write-like command detected while --intent is read")


def resolve_powershell() -> str:
    found = shutil.which("powershell.exe")
    if found:
        return found
    fixed = Path("/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe")
    if fixed.exists():
        return str(fixed)
    raise RunnerUsageError("powershell.exe is not available through WSL interop")


def windows_path(value: str | None) -> str | None:
    if not value:
        return None
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\"):
        return value
    completed = subprocess.run(
        ["wslpath", "-w", value], check=False, capture_output=True, text=True, timeout=5
    )
    if completed.returncode != 0:
        raise RunnerUsageError(f"cannot translate WSL path: {value}")
    return completed.stdout.strip()


def merge_wslenv(existing: str, names: Iterable[str]) -> str:
    entries = [entry for entry in existing.split(":") if entry]
    present = {entry.split("/", 1)[0] for entry in entries}
    for name in names:
        if name not in present:
            entries.append(f"{name}/w")
    return ":".join(entries)


def build_environment(args: argparse.Namespace, run_id: str) -> dict[str, str]:
    env = os.environ.copy()
    values = {
        "CODEX_WINDOWS_EXPECT_HOST": args.expect_host or "",
        "CODEX_WINDOWS_EXPECT_USER": args.expect_user or "",
        "CODEX_WINDOWS_CWD": windows_path(args.cwd) or "",
        "CODEX_WINDOWS_INTENT": args.intent,
        "CODEX_WINDOWS_RUN_ID": run_id,
        "CODEX_WINDOWS_INPUT_PATH": windows_path(args.input_json) or "",
    }
    env.update(values)
    env["WSLENV"] = merge_wslenv(env.get("WSLENV", ""), values)
    return env


def extract_result(stderr: str) -> tuple[str, dict[str, object] | None]:
    clean: list[str] = []
    result: dict[str, object] | None = None
    for line in stderr.splitlines():
        if line.startswith(MARKER):
            try:
                result = json.loads(line[len(MARKER) :])
            except json.JSONDecodeError:
                clean.append(line)
        else:
            clean.append(line)
    suffix = "\n" if stderr.endswith("\n") and clean else ""
    return "\n".join(clean) + suffix, result


def transport_result(mode: str, stderr: str, returncode: int) -> dict[str, object]:
    lowered = stderr.lower()
    if "utilacceptvsock" in lowered or "utilbindvsock" in lowered or "socket failed" in lowered:
        kind = "interop_transport"
    elif "cannot execute binary file" in lowered or "exec format error" in lowered:
        kind = "interop_binary_dispatch"
    else:
        kind = "missing_result_envelope"
    return {
        "schema": SCHEMA,
        "ok": False,
        "mode": mode,
        "target": "local",
        "process_exit_code": returncode,
        "native_exit_code": None,
        "timed_out": False,
        "error_kind": kind,
        "message": "Windows runner did not return its required result envelope.",
        "cleanup_status": "unknown",
    }


def launch_exception_result(mode: str | None, exc: BaseException) -> dict[str, object]:
    """Classify failures that occur before a Windows child can emit an envelope."""
    message = str(exc)
    lowered = message.lower()
    error_number = getattr(exc, "errno", None)
    if error_number == errno.ENOEXEC or "exec format error" in lowered:
        kind = "interop_binary_dispatch"
    elif error_number in {errno.ETIMEDOUT, errno.ECONNRESET, errno.ECONNREFUSED} or any(
        token in lowered for token in ("utilacceptvsock", "utilbindvsock", "socket failed")
    ):
        kind = "interop_transport"
    else:
        kind = "runner_preflight"
    return {
        "schema": SCHEMA,
        "ok": False,
        "mode": mode,
        "target": "local",
        "process_exit_code": 64,
        "native_exit_code": None,
        "timed_out": False,
        "error_kind": kind,
        "message": message,
        "cleanup_status": "not_started",
    }


def execute(args: argparse.Namespace, source: str) -> Execution:
    safety_check(source, args.intent)
    if args.intent == "write":
        if not args.expect_host:
            raise RunnerUsageError("--intent write requires --expect-host")
        if not args.backup_receipt or not Path(args.backup_receipt).is_file():
            raise RunnerUsageError("--intent write requires an existing --backup-receipt")
    if args.input_json and not Path(args.input_json).is_file():
        raise RunnerUsageError("--input-json must name an existing file")

    mode = args.mode
    bootstrap = POWERSHELL_BOOTSTRAP if mode == "powershell" else CMD_BOOTSTRAP
    run_id = uuid.uuid4().hex
    env = build_environment(args, run_id)
    argv = [
        resolve_powershell(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        bootstrap,
    ]
    if args.dry_run:
        result = {
            "schema": SCHEMA,
            "ok": True,
            "mode": mode,
            "target": "local",
            "dry_run": True,
            "argv_has_task_source": source in argv,
            "intent": args.intent,
        }
        return Execution(0, "", "", result)

    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        shell=False,
    )
    try:
        stdout_b, stderr_b = proc.communicate(source.encode("utf-8"), timeout=args.timeout)
        returncode = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout_b, stderr_b = proc.communicate()
        returncode = 124
        timed_out = True

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    stderr, result = extract_result(stderr)
    if timed_out:
        result = {
            "schema": SCHEMA,
            "ok": False,
            "mode": mode,
            "target": "local",
            "process_exit_code": 124,
            "native_exit_code": None,
            "timed_out": True,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "error_kind": "timeout",
            "message": "Windows command exceeded the configured timeout; do not retry until process state is checked.",
            "cleanup_status": "unknown_after_forced_timeout",
        }
    elif result is None:
        result = transport_result(mode, stderr, returncode)
    return Execution(returncode, stdout, stderr, result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run transparent PowerShell or cmd source from WSL without nested-shell quoting."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for mode in ("powershell", "cmd"):
        sub = subparsers.add_parser(mode)
        sub.add_argument("script", nargs="?", default="-", help="UTF-8 source file, or - for stdin")
        sub.add_argument("--intent", choices=("read", "write"), default="read")
        sub.add_argument("--expect-host")
        sub.add_argument("--expect-user")
        sub.add_argument("--cwd", help="Windows path or WSL path to use as the Windows working directory")
        sub.add_argument("--input-json", help="JSON data file exposed as CODEX_WINDOWS_INPUT_PATH")
        sub.add_argument("--backup-receipt", help="required existing receipt for write intent")
        sub.add_argument("--timeout", type=float, default=30.0)
        sub.add_argument("--json-only", action="store_true")
        sub.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        source = read_source(args.script)
        execution = execute(args, source)
    except RunnerUsageError as exc:
        result = launch_exception_result(getattr(args, "mode", None), exc)
        print(MARKER + json.dumps(result, separators=(",", ":")), file=sys.stderr)
        return 64
    except (OSError, subprocess.SubprocessError) as exc:
        # WHY: when binfmt/vsock fails, Popen may throw before PowerShell starts.
        # Preserve the interop owner layer instead of calling it a syntax error.
        result = launch_exception_result(getattr(args, "mode", None), exc)
        print(MARKER + json.dumps(result, separators=(",", ":")), file=sys.stderr)
        return 64

    if args.json_only:
        print(json.dumps(execution.result, separators=(",", ":")))
    else:
        if execution.stdout:
            sys.stdout.write(execution.stdout)
        if execution.stderr:
            sys.stderr.write(execution.stderr)
        print(MARKER + json.dumps(execution.result, separators=(",", ":")), file=sys.stderr)
    return execution.returncode if execution.returncode != 0 else (0 if execution.result.get("ok") else 1)


if __name__ == "__main__":
    raise SystemExit(main())
