#!/usr/bin/env python3
"""Route Windows prompts with bounded, plugin-owned same-turn skill context."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from typing import Any


# WHY: UserPromptSubmit does not support a matcher, so this classifier must stay
# silent for unrelated prompts. The fixture suite protects both the old missed
# Windows route and the non-Windows no-op boundary.
POWERSHELL_RE = re.compile(
    r"\b(?:powershell(?:\.exe)?|pwsh(?:\.exe)?)\b"
    r"|(?<!\w)\$(?:env:|psversiontable|lastexitcode)\b",
    re.IGNORECASE,
)
CMD_RE = re.compile(
    r"\bcmd(?:\.exe)?\b"
    r"|\bcommand prompt\b"
    r"|\b(?:batch|bat|cmd)\s+(?:file|script)\b"
    r"|\.(?:bat|cmd)\b",
    re.IGNORECASE,
)
WINDOWS_NATIVE_RE = re.compile(
    r"\b(?:winget|msiexec|regedit|wevtutil|schtasks|netsh|robocopy|icacls|"
    r"tasklist|taskkill|dism|sfc|sc)(?:\.exe)?\b",
    re.IGNORECASE,
)
WINDOWS_PATH_RE = re.compile(
    r"\b[a-z]:\\|\b(?:hklm|hkcu|hkcr|hku|hkcc)(?::|\\)|%[a-z_][a-z0-9_]*%",
    re.IGNORECASE,
)
WINDOWS_TECH_RE = re.compile(
    r"\bwindows\s+(?:10|11|server|pc|host|machine|computer|registry|services?|"
    r"process(?:es)?|firewall|event\s+logs?|scheduled\s+tasks?|installers?|"
    r"packages?|disks?|volumes?|network|paths?|files?|commands?|terminal)\b"
    r"|\b(?:uac|ntfs|event viewer|task scheduler|windows registry)\b",
    re.IGNORECASE,
)
WINDOWS_ACTION_RE = re.compile(
    r"\b(?:run|execute|write|build|inspect|list|check|query|change|configure|"
    r"install|uninstall|remove|delete|start|stop|restart|fix|troubleshoot)\b"
    r".{0,60}\b(?:on|in|from|to|for)\s+(?:a\s+)?windows\b"
    r"|\bwindows\b.{0,80}\b(?:command|script|shell|terminal|service|registry|"
    r"process|firewall|installer|package|event log|scheduled task)\b",
    re.IGNORECASE | re.DOTALL,
)
WSL_RE = re.compile(
    r"\b(?:wsl2?|wsl\.exe|windows subsystem for linux|wslpath|wsl_interop)\b",
    re.IGNORECASE,
)
WSL_INTEROP_RE = re.compile(
    r"\b(?:interop|vsock|relay|windows|powershell|pwsh|cmd(?:\.exe)?|"
    r"wslpath)\b|/mnt/[a-z]/|\.exe\b",
    re.IGNORECASE,
)
LINUX_ONLY_RE = re.compile(
    r"\b(?:bash|zsh|fish|linux|ubuntu|debian|fedora|arch|apt(?:-get)?|dnf|"
    r"pacman|systemd|grep|sed|awk)\b",
    re.IGNORECASE,
)
SSH_RE = re.compile(r"\bssh\b|\bopenssh\b", re.IGNORECASE)
OUTWARD_COPY_RE = re.compile(
    r"\b(?:title|headline|readme|tagline|copywriting|outward-facing\s+copy|"
    r"repository\s+(?:topics?|description)|github\s+(?:topics?|description)|"
    r"searchable|search\s+terms?|keywords?)\b",
    re.IGNORECASE,
)
WINDOWS_WORKFLOW_INTENT_RE = re.compile(
    r"\b(?:run|execute|invoke|launch|inspect|query|install|uninstall|remove|"
    r"delete|start|stop|restart|configure|diagnose|troubleshoot|debug)\b"
    r"|\b(?:powershell(?:\.exe)?|pwsh(?:\.exe)?|cmd(?:\.exe)?|command|script|"
    r"executable|interop|vsock|relay|wslpath|wsl_interop|parser|syntax|quoting|"
    r"encoding|timeout|clixml|transport|error|failure)\b"
    r"|\bwindows\s+(?:host|server|pc|machine|computer)\b"
    r"|\b(?:winget|msiexec|regedit|wevtutil|schtasks|netsh|robocopy|icacls|"
    r"tasklist|taskkill|dism|sfc|sc)(?:\.exe)?\b"
    r"|/mnt/[a-z]/|\.[a-z0-9_-]*exe\b",
    re.IGNORECASE,
)

SKILL_RELATIVE_PATH = Path("skills") / "run-powershell-safely" / "SKILL.md"
MAX_SKILL_BYTES = 24 * 1024
SENSITIVE_SKILL_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+\\"),
    re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+/"),
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"\b(?:AKIA[0-9A-Z]{16}|gh[opsu]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,})\b"),
)


def classify_prompt(prompt: str) -> bool:
    """Return whether a prompt needs the bundled Windows command workflow."""

    text = " ".join(prompt.split())
    if not text:
        return False

    # WHY: Repository copy frequently names Windows and WSL as search terms.
    # Route it to copy tooling unless the same prompt also contains concrete
    # command, host, interop, native-tool, or failure intent.
    if OUTWARD_COPY_RE.search(text) and not WINDOWS_WORKFLOW_INTENT_RE.search(text):
        return False

    direct_windows_signal = any(
        pattern.search(text)
        for pattern in (
            POWERSHELL_RE,
            CMD_RE,
            WINDOWS_NATIVE_RE,
            WINDOWS_PATH_RE,
            WINDOWS_TECH_RE,
            WINDOWS_ACTION_RE,
        )
    )
    if direct_windows_signal:
        return True

    if WSL_RE.search(text):
        if LINUX_ONLY_RE.search(text) and not WSL_INTEROP_RE.search(text):
            return False
        return True

    # Generic SSH belongs to its target OS. Only route it when the prompt also
    # identifies a Windows target; Linux SSH must remain untouched.
    if SSH_RE.search(text) and re.search(
        r"\bwindows(?:\s+(?:server|host|pc|machine|computer))?\b",
        text,
        re.IGNORECASE,
    ):
        return True

    return False


def _extract_prompt(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("prompt", "user_prompt"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _read_payload() -> Any:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_bundled_skill() -> tuple[Path | None, str | None, str | None]:
    """Read only this plugin's bounded SKILL.md and return privacy-safe errors."""

    plugin_root_raw = os.environ.get("PLUGIN_ROOT")
    if not plugin_root_raw:
        return None, None, "plugin_root_missing"

    try:
        plugin_root = Path(plugin_root_raw).expanduser().resolve(strict=True)
        skill_path = (plugin_root / SKILL_RELATIVE_PATH).resolve(strict=True)
        skill_path.relative_to(plugin_root)
    except (OSError, RuntimeError, ValueError):
        return None, None, "skill_path_invalid"

    if not skill_path.is_file():
        return None, None, "skill_missing"

    try:
        if skill_path.stat().st_size > MAX_SKILL_BYTES:
            return None, None, "skill_oversize"
        with skill_path.open("rb") as handle:
            raw = handle.read(MAX_SKILL_BYTES + 1)
    except OSError:
        return None, None, "skill_unreadable"

    if len(raw) > MAX_SKILL_BYTES or b"\x00" in raw:
        return None, None, "skill_oversize_or_binary"
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, None, "skill_not_utf8"
    if any(pattern.search(content) for pattern in SENSITIVE_SKILL_PATTERNS):
        return None, None, "skill_privacy_check_failed"

    return skill_path, content, None


def _same_turn_context() -> str:
    skill_path, content, error = _load_bundled_skill()
    if error is not None:
        return (
            f"WINDOWS COMMAND ROUTE BLOCKED ({error}): The bundled workflow "
            "could not be loaded safely. Do not construct or run a Windows "
            "command until the plugin installation is repaired. This hook "
            "neither authorizes nor executes commands."
        )

    assert skill_path is not None and content is not None
    return (
        "WINDOWS COMMAND ROUTE (same-turn bundled context)\n"
        f"Bundled skill path (exact resolved path): {skill_path}\n"
        "Explicit skill-name resolution may already have happened before this "
        "UserPromptSubmit hook. Therefore, use the exact plugin-owned path and "
        "embedded SKILL.md below as the authoritative workflow for this turn; "
        "do not substitute another same-named standalone skill. Resolve all "
        "relative references from the SKILL.md parent directory. On macOS or "
        "Linux without local WSL interop, use its Windows-over-SSH workflow "
        "with a verified Windows target; do not use the WSL-only "
        "windows_command.py runner as an SSH client. This hook only routes "
        "model context; it neither authorizes nor executes commands.\n"
        "--- BEGIN PLUGIN-OWNED SKILL.md ---\n"
        f"{content.rstrip()}\n"
        "--- END PLUGIN-OWNED SKILL.md ---"
    )


def main() -> int:
    prompt = _extract_prompt(_read_payload())
    if not classify_prompt(prompt):
        return 0

    response = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": _same_turn_context(),
        }
    }
    json.dump(response, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
