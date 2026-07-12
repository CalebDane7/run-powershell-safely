#!/usr/bin/env python3
"""Read-only WSL-to-Windows interop health and owner-layer classifier."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import windows_command


SCHEMA = "codex.windows.interop-health.v1"
INTEROP_ERRORS = ("UtilAcceptVsock", "UtilBindVsock", "Exec format error")
DMESG_TIME = re.compile(r"^\[\s*(?P<seconds>[0-9]+(?:\.[0-9]+)?)\]")
RELAY = re.compile(r"WSL \((?P<pid>[0-9]+) - (?P<role>[^)]+)\).*?(?P<kind>Util(?:Accept|Bind)Vsock)")


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def executable(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def static_snapshot() -> dict[str, Any]:
    interop_value = os.environ.get("WSL_INTEROP")
    interop_exists = False
    interop_is_socket = False
    if interop_value:
        try:
            mode = os.stat(interop_value).st_mode
            interop_exists = True
            interop_is_socket = stat.S_ISSOCK(mode)
        except OSError:
            pass

    global_status = read_text(Path("/proc/sys/fs/binfmt_misc/status"))
    registration = read_text(Path("/proc/sys/fs/binfmt_misc/WSLInterop"))
    wsl_conf = read_text(Path("/etc/wsl.conf")) or ""
    cmd = executable(
        (
            "/mnt/c/Windows/System32/cmd.exe",
            "/mnt/c/WINDOWS/System32/cmd.exe",
        )
    )
    powershell = executable(
        (
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe",
        )
    )
    def has_mz(path: str | None) -> bool:
        if not path:
            return False
        try:
            with open(path, "rb") as handle:
                return handle.read(2) == b"MZ"
        except OSError:
            return False
    mount_text = read_text(Path("/proc/mounts")) or ""
    c_mount_present = any(
        len(parts) >= 3 and parts[1] == "/mnt/c" and parts[2] in {"9p", "drvfs"}
        for line in mount_text.splitlines()
        if (parts := line.split())
    )
    return {
        "distro": os.environ.get("WSL_DISTRO_NAME"),
        "kernel_release": os.uname().release,
        "wsl_interop_path": interop_value,
        "wsl_interop_exists": interop_exists,
        "wsl_interop_is_socket": interop_is_socket,
        "binfmt_global_enabled": bool(global_status and global_status.strip() == "enabled"),
        "binfmt_wslinterop_present": registration is not None,
        "binfmt_wslinterop_disabled": bool(registration and re.search(r"(?m)^disabled$", registration)),
        "binfmt_wslinterop_enabled": bool(
            registration
            and re.search(r"(?m)^enabled$", registration)
            and re.search(r"(?m)^interpreter /init$", registration)
            and re.search(r"(?m)^magic 4d5a", registration)
            and re.search(r"(?m)^flags: .*F", registration)
        ),
        "wsl_conf_explicitly_disables_interop": bool(
            re.search(r"(?ims)^\s*\[interop\]\s*$.*?^\s*enabled\s*=\s*false\s*$", wsl_conf)
        ),
        "init_executable": os.path.isfile("/init") and os.access("/init", os.X_OK),
        "windows_c_mount_present": c_mount_present,
        "cmd_path": cmd,
        "powershell_path": powershell,
        "cmd_has_mz_magic": has_mz(cmd),
        "powershell_has_mz_magic": has_mz(powershell),
    }


def dmesg_snapshot(recent_seconds: float) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["dmesg", "--color=never"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "message": str(exc), "recent_count": 0, "recent_relays": []}
    text = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", errors="replace").strip()
        return {"available": False, "message": message, "recent_count": 0, "recent_relays": []}

    try:
        uptime = float((read_text(Path("/proc/uptime")) or "0").split()[0])
    except (ValueError, IndexError):
        uptime = 0.0
    threshold = max(0.0, uptime - recent_seconds)
    relevant: list[str] = []
    recent: list[str] = []
    relays: dict[tuple[int, str, str], int] = {}
    for line in text.splitlines():
        if not any(token.lower() in line.lower() for token in INTEROP_ERRORS):
            continue
        relevant.append(line)
        timestamp = DMESG_TIME.match(line)
        if timestamp and float(timestamp.group("seconds")) >= threshold:
            recent.append(line)
            relay = RELAY.search(line)
            if relay:
                key = (int(relay.group("pid")), relay.group("role"), relay.group("kind"))
                relays[key] = relays.get(key, 0) + 1
    relay_rows = [
        {"pid": pid, "role": role, "error": kind, "recent_count": count}
        for (pid, role, kind), count in sorted(relays.items())
    ]
    return {
        "available": True,
        "uptime_seconds": uptime,
        "window_seconds": recent_seconds,
        "total_matching_count": len(relevant),
        "recent_count": len(recent),
        "recent_relays": relay_rows,
        # A digestable line set lets the caller detect a probe-time delta while
        # avoiding broad kernel-log output in receipts.
        "recent_fingerprints": [line[-180:] for line in recent],
    }


def decode_output(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8", errors="replace")


def cmd_probe(cmd_path: str, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [cmd_path, "/d", "/q", "/c", "ver"],
            cwd="/mnt/c/Windows" if Path("/mnt/c/Windows").is_dir() else None,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": 124, "duration_ms": int((time.monotonic() - started) * 1000), "error_kind": "timeout"}
    except OSError as exc:
        failure = windows_command.launch_exception_result("cmd", exc)
        return {"ok": False, "exit_code": 64, "duration_ms": int((time.monotonic() - started) * 1000), "error_kind": failure["error_kind"], "message": failure["message"]}
    stdout = decode_output(proc.stdout)
    stderr = decode_output(proc.stderr)
    lowered = (stdout + "\n" + stderr).lower()
    if "utilacceptvsock" in lowered or "utilbindvsock" in lowered:
        kind = "interop_transport"
    elif "exec format error" in lowered:
        kind = "interop_binary_dispatch"
    elif proc.returncode != 0:
        kind = "target_command"
    else:
        kind = None
    return {
        "ok": proc.returncode == 0 and "microsoft windows" in lowered,
        "exit_code": proc.returncode,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "error_kind": kind,
        "windows_version_seen": "microsoft windows" in lowered,
    }


def powershell_probe(expect_host: str, timeout: float) -> dict[str, Any]:
    args = windows_command.build_parser().parse_args(
        ["powershell", "--intent", "read", "--expect-host", expect_host, "--timeout", str(timeout)]
    )
    source = "[pscustomobject]@{ Probe='interop-health'; Hostname=$env:COMPUTERNAME } | ConvertTo-Json -Compress\n"
    try:
        execution = windows_command.execute(args, source)
    except (windows_command.RunnerUsageError, OSError, subprocess.SubprocessError) as exc:
        result = windows_command.launch_exception_result("powershell", exc)
        return {"ok": False, "exit_code": 64, "error_kind": result["error_kind"], "message": result["message"]}
    result = execution.result
    output_host = None
    try:
        output_host = json.loads(execution.stdout).get("Hostname")
    except (json.JSONDecodeError, AttributeError):
        pass
    return {
        "ok": bool(result.get("ok")) and output_host and output_host.lower() == expect_host.lower(),
        "exit_code": execution.returncode,
        "duration_ms": result.get("duration_ms"),
        "error_kind": result.get("error_kind"),
        "hostname": result.get("hostname"),
        "powershell_version": result.get("powershell_version"),
        "result_envelope_present": result.get("schema") == windows_command.SCHEMA,
    }


def classify(static: dict[str, Any], cmd: dict[str, Any] | None, powershell: dict[str, Any] | None, logs: dict[str, Any], new_log_count: int) -> tuple[str, str, bool]:
    if static.get("wsl_conf_explicitly_disables_interop"):
        return "INTEROP_DISABLED_BY_CONFIG", "Stop and ask the WSL configuration owner; any config change requires backup and a stopped/restarted distro.", True
    if static.get("binfmt_wslinterop_present") and static.get("binfmt_wslinterop_disabled"):
        return "EXISTING_HANDLER_DISABLED", "An authorized Linux-root owner may use Microsoft's narrow session repair to echo 1 to the existing WSLInterop entry, then re-read it and run one bounded probe.", False
    if not static.get("windows_c_mount_present"):
        return "WINDOWS_MOUNT_UNAVAILABLE", "The Windows filesystem owner must restore the /mnt/c boundary before Windows executable proof; do not change PowerShell source.", True
    if not static.get("cmd_path") or not static.get("powershell_path"):
        return "TARGET_NOT_FOUND", "Resolve the exact Windows executable or installation owner; a missing file/path is not proof of an interop transport outage.", False
    if not static.get("cmd_has_mz_magic") or not static.get("powershell_has_mz_magic"):
        return "TARGET_BINARY_INVALID", "The exact target is not a valid PE MZ file; stop and use the Windows file/vendor owner rather than repairing WSLInterop.", True
    static_required = (
        "wsl_interop_exists",
        "wsl_interop_is_socket",
        "binfmt_global_enabled",
        "binfmt_wslinterop_enabled",
        "init_executable",
    )
    if not all(static.get(name) for name in static_required):
        return "BROKEN_STATIC_REGISTRATION", "Preserve work and use the external Windows WSL owner to recover or relaunch the distro; do not rewrite PowerShell source.", True
    if cmd is None or powershell is None:
        return "STATIC_ONLY", "Run one bounded serialized cmd probe and one fixed-runner probe before claiming interop works.", False
    error_kinds = {cmd.get("error_kind"), powershell.get("error_kind")}
    if "interop_binary_dispatch" in error_kinds:
        return "BROKEN_BINARY_DISPATCH", "Preserve work and recover WSL from Windows at a safe boundary; do not alter valid command syntax.", True
    if "interop_transport" in error_kinds or "timeout" in error_kinds:
        return "BROKEN_TRANSPORT", "Stop retries, preserve the session, and collect/recover from the external Windows WSL owner boundary.", True
    if not cmd.get("ok") or not powershell.get("ok"):
        return "BROKEN_TARGET_OR_RUNNER", "Inspect the exact target result and runner envelope before retrying; do not classify it as WSL transport without evidence.", False
    if not logs.get("available"):
        return "USABLE_LOGS_UNVERIFIED", "Interop probes pass, but kernel relay health is unverified; keep commands serialized and recheck logs from an authorized owner.", False
    if logs.get("recent_count", 0) or new_log_count:
        return "TRANSIENT_INTEROP_WARNING", "The fresh bounded probes pass, so record warning timing and continue with the deterministic runner. Do not restart; escalate only if a requested result, envelope, or next one-shot probe fails.", False
    return "HEALTHY", "Use the deterministic runner; recheck after any WSL update/restart, socket change, timeout, or missing result envelope.", False


def build_report(expect_host: str, timeout: float, recent_log_seconds: float, probe: bool = True) -> dict[str, Any]:
    static = static_snapshot()
    before = dmesg_snapshot(recent_log_seconds)
    cmd = None
    powershell = None
    if probe and static.get("cmd_path"):
        cmd = cmd_probe(str(static["cmd_path"]), timeout)
    if probe and static.get("powershell_path"):
        powershell = powershell_probe(expect_host, timeout)
    after = dmesg_snapshot(recent_log_seconds)
    before_fingerprints = before.get("recent_fingerprints", []) if before.get("available") else []
    after_fingerprints = after.get("recent_fingerprints", []) if after.get("available") else []
    new_count = max(0, len(after_fingerprints) - len(before_fingerprints))
    classification, action, external_owner = classify(static, cmd, powershell, after, new_count)
    after.pop("recent_fingerprints", None)
    return {
        "schema": SCHEMA,
        "classification": classification,
        "usable": bool(cmd and cmd.get("ok") and powershell and powershell.get("ok")),
        "requires_external_windows_owner": external_owner,
        "static": static,
        "cmd_probe": cmd,
        "powershell_probe": powershell,
        "kernel_log": {**after, "new_matching_count_during_probe": new_count},
        "safe_next_action": action,
        "forbidden_repeats": [
            "do not rewrite valid PowerShell after a vsock or binary-dispatch failure",
            "do not loop Windows executable probes",
            "do not kill Relay processes from PID or age alone",
            "do not run wsl --shutdown or --terminate from the active WSL session",
            "do not weaken endpoint security, firewall, SSH, or WSL security settings",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only WSL-to-Windows interop health classifier")
    parser.add_argument("--expect-host", required=True)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--recent-log-seconds", type=float, default=180.0)
    parser.add_argument("--no-probe", action="store_true", help="inspect static registration and logs without launching Windows executables")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(args.expect_host, args.timeout, args.recent_log_seconds, not args.no_probe)
    print(json.dumps(report, separators=(",", ":"), ensure_ascii=False))
    if report["classification"] == "HEALTHY":
        return 0
    if report["classification"] == "TRANSIENT_INTEROP_WARNING":
        return 2
    if report["classification"] in {"USABLE_LOGS_UNVERIFIED", "STATIC_ONLY"}:
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
