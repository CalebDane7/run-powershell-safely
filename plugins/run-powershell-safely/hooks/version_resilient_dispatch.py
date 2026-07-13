#!/usr/bin/env python3
"""Run the current Windows router through a strictly nonblocking boundary."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any


EVENT_NAME = "UserPromptSubmit"
ROUTE_RELATIVE_PATH = Path("hooks") / "windows_prompt_route.py"
FALLBACK_WINDOWS_RE = re.compile(
    r"\b(?:powershell(?:\.exe)?|pwsh(?:\.exe)?|cmd\.exe|command prompt|"
    r"winget|msiexec|regedit|wevtutil|schtasks|netsh|robocopy|icacls|"
    r"tasklist|taskkill|dism|windows registry|windows services?|"
    r"windows firewall|windows commands?|windows (?:host|server|pc|machine|"
    r"computer)|wsl2?|windows subsystem for linux|wsl_interop|wslpath)\b"
    r"|\b[a-z]:\\|\b(?:hklm|hkcu)(?::|\\)|/mnt/[a-z]/",
    re.IGNORECASE,
)
COPY_SURFACE_RE = re.compile(
    r"\b(?:title|headline|readme|tagline|copywriting|outward-facing copy|"
    r"repository topics?|repository description|github topics?|"
    r"github description|searchable|search terms?|keywords?)\b",
    re.IGNORECASE,
)
WORKFLOW_INTENT_RE = re.compile(
    r"\b(?:run|execute|invoke|launch|inspect|query|install|uninstall|remove|"
    r"delete|start|stop|restart|configure|diagnose|troubleshoot|debug|"
    r"command|script|interop|parser|syntax|quoting|encoding|timeout|"
    r"clixml|transport|error|failure)\b",
    re.IGNORECASE,
)


def _extract_prompt(raw: str) -> tuple[str, bool]:
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except (TypeError, ValueError):
        return "", False
    if not isinstance(payload, dict):
        return "", False
    for key in ("prompt", "user_prompt"):
        value = payload.get(key)
        if isinstance(value, str):
            return value, True
    return "", False


def _fallback_match(prompt: str) -> bool:
    text = " ".join(prompt.split())
    if COPY_SURFACE_RE.search(text) and not WORKFLOW_INTENT_RE.search(text):
        return False
    return FALLBACK_WINDOWS_RE.search(text) is not None


def _degraded_context(reason: str) -> str:
    return (
        f"WINDOWS COMMAND ROUTING DEGRADED ({reason}). Continue the user's "
        "task; this guidance hook must not prevent the turn from running. "
        "If this is not a Windows command task, proceed normally. If it is, "
        "repair or reinstall the run-powershell-safely plugin, or load a "
        "verified standalone run-powershell-safely skill before executing "
        "commands. Keep the target host, shell, intent, quoting, execution, "
        "and post-state proof separate. Do not use encoded or obfuscated "
        "command transport. This hook neither authorizes nor executes commands."
    )


def _emit_degraded(reason: str) -> None:
    response = {
        "hookSpecificOutput": {
            "hookEventName": EVENT_NAME,
            "additionalContext": _degraded_context(reason),
        }
    }
    json.dump(response, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def _load_route(plugin_root: Path) -> ModuleType:
    route_path = (plugin_root / ROUTE_RELATIVE_PATH).resolve(strict=True)
    route_path.relative_to(plugin_root)
    spec = importlib.util.spec_from_file_location("_plugin_windows_prompt_route", route_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("route_loader_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validated_output(raw_output: str) -> str | None:
    if not raw_output.strip():
        return ""
    try:
        response = json.loads(raw_output)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    # WHY: the old regression reached Codex through hook control fields. This
    # boundary only permits model-visible UserPromptSubmit context, so a future
    # router mistake degrades to guidance instead of vetoing the user's turn.
    if not isinstance(response, dict) or set(response) != {"hookSpecificOutput"}:
        return None
    output = response.get("hookSpecificOutput")
    if not isinstance(output, dict) or set(output) != {
        "hookEventName",
        "additionalContext",
    }:
        return None
    if output.get("hookEventName") != EVENT_NAME:
        return None
    if not isinstance(output.get("additionalContext"), str):
        return None
    return json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"


def _call_route(module: ModuleType, raw: str) -> str:
    main = getattr(module, "main", None)
    if not callable(main):
        raise RuntimeError("route_main_missing")
    prior_input = sys.stdin
    prior_output = sys.stdout
    captured = io.StringIO()
    try:
        sys.stdin = io.StringIO(raw)
        sys.stdout = captured
        result = main()
    finally:
        sys.stdin = prior_input
        sys.stdout = prior_output
    if result not in (None, 0):
        raise RuntimeError("route_nonzero")
    return captured.getvalue()


def _qualified(module: ModuleType | None, prompt: str) -> bool:
    classifier: Any = getattr(module, "classify_prompt", None) if module else None
    if callable(classifier):
        try:
            return bool(classifier(prompt))
        except BaseException:
            pass
    return _fallback_match(prompt)


def main() -> int:
    try:
        raw = sys.stdin.read()
    except BaseException:
        try:
            _emit_degraded("event_read_failed")
        except BaseException:
            pass
        return 0
    prompt, event_valid = _extract_prompt(raw)
    module: ModuleType | None = None
    try:
        plugin_root = Path(os.environ.get("PLUGIN_ROOT", "")).expanduser().resolve(
            strict=True
        )
        module = _load_route(plugin_root)
        output = _validated_output(_call_route(module, raw))
        if output is None:
            if not event_valid:
                _emit_degraded("event_schema_invalid")
            elif _qualified(module, prompt):
                _emit_degraded("route_output_invalid")
        elif output:
            sys.stdout.write(output)
    except BaseException:
        if not event_valid or _qualified(module, prompt):
            try:
                reason = "route_unavailable" if event_valid else "event_schema_invalid"
                _emit_degraded(reason)
            except BaseException:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
