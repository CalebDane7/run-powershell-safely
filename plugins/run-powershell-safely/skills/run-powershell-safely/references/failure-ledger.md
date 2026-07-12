# Failure Ledger

This ledger summarizes recurring cross-shell and WSL-to-Windows failure signatures. Keep entries generic and reproducible.

| failure signature | owner layer | durable response |
| --- | --- | --- |
| `= : The term '=' is not recognized`; `.Id` cannot convert | Bash expanded `$p` inside a double-quoted PowerShell string | Use the deterministic runner and a quoted heredoc; never put task source in Bash double quotes. |
| `/bin/bash.ProcessName` appears inside PowerShell | Bash expanded `$_` | Same runner boundary; do not add ad hoc escapes. |
| `An empty pipe element is not allowed` after `foreach (...) { ... } | ...` | PowerShell grammar | Collect the `foreach` output into `$rows`, then pipe `$rows`. |
| a fresh agent uses Bash `\` line continuation and PowerShell reports a parse or empty-pipe error | cross-shell habit leaked into PowerShell grammar | Break after a PowerShell pipe/operator or use balanced parentheses; keep the whole-source parse-before-run regression test. |
| raw `powershell.exe -Command -` prints a parser error but exits zero | statement-by-statement stdin execution | Buffer all source and compile with `ScriptBlock.Create` before running. Require the result envelope. |
| `UtilAcceptVsockAnyPort`, `UtilBindVsockAnyPort`, socket error 110 | WSL interop transport | One health recheck maximum, then stop. Do not rewrite valid PowerShell or repeatedly relaunch Windows executables. Recover WSL from Windows at a safe boundary. |
| cmd/PowerShell probes pass while long-lived `Relay` PIDs keep logging abnormal `UtilAcceptVsock` waits | transient background relay warning | Classify `TRANSIENT_INTEROP_WARNING`, record timing/rate, and continue serialized launches. Escalate only if the requested result, result envelope, or next one-shot probe fails. Never kill relays from PID/age alone. |
| `cannot execute binary file: Exec format error` for `.exe` | WSL binary dispatch | Stop Windows interop calls; Linux `/mnt/c` reads may still work. Do not delete through Linux merely to work around it. |
| cmd says UNC paths are unsupported | Windows native cwd starts in `\\wsl.localhost` | Set an explicit Windows cwd or let the runner default to the Windows user profile. |
| native command fails but PowerShell appears successful | `$LASTEXITCODE` not propagated | Reset, inspect, and explicitly map `$LASTEXITCODE`; do not infer from stderr. |
| huge `#< CLIXML`/progress output | PowerShell progress/stream serialization | Use noninteractive mode, suppress progress, and use JSON for structured output. |
| UTF-8 JSON returns mojibake such as `cafÃ©` | Windows PowerShell 5.1 `Get-Content` defaulted to the legacy code page | Pass `--input-json` and consume the runner-provided `$CodexInputData`, which is decoded explicitly as UTF-8 before task execution. |
| process disappears before UI Automation inspection | process/UI race | Capture exact PID, main window state, and timestamps; re-read live process state instead of treating absence as parser failure. |
| `Access denied` or CIM resource unavailable | ACL/UAC/owner boundary | Stop and use the real elevation/owner flow; do not bypass execution policy or security controls. |
| endpoint security blocks a command | opaque or suspicious command transport | Retire encoded or obfuscated transport. Keep source readable; stop on alerts and never add exclusions or disable protection. |

When a new failure appears, record the exact command boundary, error, classification, fix, falsifier, and regression test. Do not add machine-specific destructive targets to this general skill.
