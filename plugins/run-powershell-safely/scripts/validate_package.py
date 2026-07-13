#!/usr/bin/env python3
"""Validate the plugin package and its embedded update-survival launcher."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DISPATCH_PATH = PLUGIN_ROOT / "hooks" / "version_resilient_dispatch.py"
ROUTE_PATH = PLUGIN_ROOT / "hooks" / "windows_prompt_route.py"
HOOKS_PATH = PLUGIN_ROOT / "hooks" / "hooks.json"
MANIFEST_PATH = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
EXPECTED_VERSION = "1.0.3"
FAIL_OPEN_CONTEXT = (
    "WINDOWS COMMAND ROUTING DEGRADED. Continue the current user task; this guidance "
    "hook cannot block the turn. If this is not Windows work, proceed normally. "
    "If it is, update or reinstall run-powershell-safely, or load its verified "
    "standalone skill before commands. Never use encoded or obfuscated command "
    "transport."
)
FAIL_OPEN_LITERAL = "'" + FAIL_OPEN_CONTEXT.replace("\\", "\\\\").replace(
    "'", "\\'"
) + "'"
INLINE_CODE = (
    "import glob,json,os,re,subprocess,sys;from pathlib import Path;"
    "o=Path(os.environ.get('PLUGIN_ROOT',''));"
    "v=re.compile(r'(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.]"
    "(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?(?:[+]([0-9A-Za-z.-]+))?');"
    "m=o/'.codex-plugin/plugin.json';d=o/'hooks/version_resilient_dispatch.py';"
    "fallback=o if m.is_file() and d.is_file() else None;"
    "paths=[Path(x) for x in glob.glob(str(o.parent/'*'))];"
    "records=[(p,json.loads((p/'.codex-plugin/plugin.json').read_text"
    "(encoding='utf-8'))) for p in paths if not p.is_symlink() and "
    "(p/'.codex-plugin/plugin.json').is_file() and "
    "(p/'hooks/version_resilient_dispatch.py').is_file()];"
    "records=[(p,a,v.fullmatch(p.name),v.fullmatch(str(a.get('version','')))) "
    "for p,a in records if a.get('name')=='run-powershell-safely'];"
    "paths=[p for p,a,x,y in records if x and y and "
    "x.groups()[:4]==y.groups()[:4] and (p.name==a['version'] or "
    "(x.group(5) or '').startswith('codex.local-'))];"
    "root=max(paths,key=lambda p:(tuple(map(int,v.fullmatch(p.name).groups()[:3])),"
    "1 if v.fullmatch(p.name).group(4) is None else 0,"
    "tuple((0,int(x)) if x.isdigit() else (1,x) for x in "
    "(v.fullmatch(p.name).group(4) or '').split('.')),p.name),default=fallback);"
    "event=sys.stdin.buffer.read(2097153);payload=json.loads(event) if event else None;"
    "prompt=payload.get('prompt',payload.get('user_prompt')) "
    "if isinstance(payload,dict) else None;"
    "schema=isinstance(prompt,str) and len(event)<=2097152;"
    "text=' '.join(prompt.split()) if schema else '';"
    "win=re.compile(r'\\b(?:powershell(?:[.]exe)?|pwsh(?:[.]exe)?|cmd[.]exe|"
    "winget|msiexec|regedit|wevtutil|schtasks|netsh|robocopy|icacls|tasklist|"
    "taskkill|dism|windows|wsl2?|wslpath|wsl_interop)\\b|\\b[a-z]:'"
    "+re.escape(chr(92))+r'|\\b(?:hklm|hkcu)(?::|'"
    "+re.escape(chr(92))+r')|/mnt/[a-z]/',re.I);"
    "copy=re.compile(r'\\b(?:title|headline|readme|tagline|copywriting|"
    "outward-facing copy|repository topics?|repository description|github topics?|"
    "github description|searchable|search terms?|keywords?)\\b',re.I);"
    "intent=re.compile(r'\\b(?:run|execute|invoke|launch|inspect|query|install|"
    "uninstall|remove|delete|start|stop|restart|configure|diagnose|troubleshoot|"
    "debug|command|script|interop|parser|syntax|quoting|encoding|timeout|clixml|"
    "transport|error|failure)\\b',re.I);"
    "need=not schema or bool(win.search(text) and not "
    "(copy.search(text) and not intent.search(text)));"
    "env=os.environ.copy();env['PLUGIN_ROOT']=str(root or o);"
    "process=subprocess.run([sys.executable,str(root/'hooks/version_resilient_dispatch.py')],"
    "input=event,capture_output=True,env=env,timeout=2) if root else None;"
    "parsed=json.loads(process.stdout) if process is not None and "
    "process.returncode==0 and process.stdout and len(process.stdout)<=262144 else None;"
    "normalized=json.dumps(parsed,separators=(',',':')) if isinstance(parsed,dict) else '';"
    "hook=parsed.get('hookSpecificOutput') if isinstance(parsed,dict) else None;"
    "valid=isinstance(parsed,dict) and set(parsed)=={'hookSpecificOutput'} and "
    "isinstance(hook,dict) and set(hook)=={'hookEventName','additionalContext'} and "
    "hook.get('hookEventName')=='UserPromptSubmit' and "
    "isinstance(hook.get('additionalContext'),str);"
    "fallback_output=json.dumps({'hookSpecificOutput':{'hookEventName':"
    "'UserPromptSubmit','additionalContext':%s}},separators=(',',':')).encode();"
    "empty=process is not None and process.returncode==0 and not process.stdout;"
    "output=normalized.encode() if valid else b'' if empty and not need else "
    "fallback_output if need else b'';sys.stdout.buffer.write(output)"
    % FAIL_OPEN_LITERAL
)


def expected_hooks_config() -> dict[str, Any]:
    # WHY: the loaded command must outlive a deleted cache directory, but it
    # stays directly reviewable so antivirus and users see ordinary Python.
    if '"' in INLINE_CODE:
        raise ValueError("inline bootstrap must not contain double quotes")
    fallback_json = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": FAIL_OPEN_CONTEXT,
            }
        },
        separators=(",", ":"),
    )
    return {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                f'python3 -c "{INLINE_CODE}" 2>/dev/null || '
                                f"printf '%s\\n' {shlex.quote(fallback_json)}"
                            ),
                            "commandWindows": (
                                f'python -c "{INLINE_CODE}" 2>nul || echo {fallback_json}'
                            ),
                            "timeout": 5,
                            "statusMessage": "Loading the Windows command workflow",
                        }
                    ]
                }
            ]
        }
    }


def _compact(source: str) -> str:
    return "".join(source.casefold().split())


def validate() -> list[str]:
    errors: list[str] = []
    required = (DISPATCH_PATH, ROUTE_PATH, HOOKS_PATH, MANIFEST_PATH)
    for path in required:
        if not path.is_file():
            errors.append(f"missing required file: {path.relative_to(PLUGIN_ROOT)}")
    if errors:
        return errors

    try:
        expected = expected_hooks_config()
    except (OSError, ValueError) as error:
        errors.append(str(error))
        return errors

    try:
        actual = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"invalid hooks.json: {error}")
    else:
        if actual != expected:
            errors.append("hooks.json does not match the embedded bootstrap source")
        handler = expected["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        if len(handler["commandWindows"]) >= 8191:
            errors.append("commandWindows exceeds the cmd.exe command-length limit")
        if max(len(handler["command"]), len(handler["commandWindows"])) > 5000:
            errors.append("inline hook command is no longer concise and reviewable")
        if "timeout=2" not in handler["command"] or handler["timeout"] <= 2:
            errors.append("dispatcher timeout must remain below the hook timeout")
        for command in (handler["command"], handler["commandWindows"]):
            if "EncodedCommand" in command or "base64" in command.casefold():
                errors.append("hook command must stay plain and readable")
            if "exec(" in command:
                errors.append("hook command must not embed an escaped exec payload")
            if "||" not in command:
                errors.append("hook command is missing its outer fail-open fallback")
            if "PLUGIN_ROOT/hooks/windows_prompt_route.py" in command:
                errors.append("hook command still depends on its original version path")

    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"invalid plugin manifest: {error}")
    else:
        if manifest.get("name") != "run-powershell-safely":
            errors.append("plugin manifest name mismatch")
        if manifest.get("version") != EXPECTED_VERSION:
            errors.append(f"plugin version must be {EXPECTED_VERSION}")
        if manifest.get("skills") != "./skills/":
            errors.append("plugin skills path mismatch")
        if "hooks" in manifest:
            errors.append("unsupported hooks field must not be in plugin.json")

    try:
        compile(INLINE_CODE, "<inline-bootstrap>", "exec")
    except SyntaxError as error:
        errors.append(f"invalid inline bootstrap: {error}")

    inline_compact = _compact(INLINE_CODE)
    for forbidden in (
        '"decision":"block"',
        "'decision':'block'",
        '"continue":false',
        "'continue':false",
        "return2",
        "exit(2)",
    ):
        if forbidden in inline_compact:
            errors.append(f"blocking hook control found in inline bootstrap: {forbidden}")

    for path in (DISPATCH_PATH, ROUTE_PATH):
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
        except (OSError, SyntaxError) as error:
            errors.append(f"invalid active hook source {path.name}: {error}")
            continue
        compact = _compact(source)
        for forbidden in (
            '"decision":"block"',
            "'decision':'block'",
            '"continue":false',
            "'continue':false",
            "return2",
            "exit(2)",
        ):
            if forbidden in compact:
                errors.append(f"blocking hook control found in {path.name}: {forbidden}")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Package validation passed: {PLUGIN_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
