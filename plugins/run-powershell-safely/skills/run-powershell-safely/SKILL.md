---
name: run-powershell-safely
description: Run and troubleshoot Windows PowerShell, cmd, and native Windows commands reliably from WSL, with separate safety guidance for Windows over SSH. Use for Windows host inspection, files, processes, services, registry, networking, packages, scheduled tasks, event logs, disks, installers, or any command containing PowerShell variables, pipelines, quotes, multiline code, paths with spaces, JSON, native executables, remote Windows targets, UAC boundaries, or WSL interop errors. Also use whenever a prior Windows command had a parser, quoting, exit-code, encoding, wrong-host, timeout, CLIXML, endpoint-security, or transport failure.
---

# Run Windows Commands Safely

Use the deterministic runner instead of constructing dynamic `powershell.exe -Command` strings. Treat shell choice, target host, intent, parsing, execution, and proof as separate decisions.

The bundled runner requires Python 3.10+, Bash, and a standard WSL setup with the Windows system drive mounted at `/mnt/c` and `powershell.exe`/`cmd.exe` available. On macOS or Linux, use the separate SSH guidance with a verified Windows target; the bundled runner is not an SSH client. Its source checks are advisory preflight guards, not an authorization boundary or security sandbox. Run only trusted, reviewed source.

## Core workflow

1. Resolve the target first. For local WSL, set `WINDOWS_EXPECT_HOST` to the verified Windows computer name. For SSH, set `WINDOWS_SSH_TARGET` and the expected remote identity. Never infer a host or user from casual wording.
2. Classify the task as `read` or `write`. Before `write`, require the exact host, a timestamped backup/manifest, literal targets, and a post-state proof.
3. Choose the owner shell:
   - Use PowerShell cmdlets for Windows state and object pipelines.
   - Use a native `.exe` directly with an argument array when no shell language is needed.
   - Use cmd only for cmd built-ins or intentional batch syntax.
4. Discover rather than guess unfamiliar syntax. Read [references/powershell-command-map.md](references/powershell-command-map.md) and use `Get-Command`, `Get-Help -Full`, and `Get-Member`.
5. Let `SKILL_DIR` be the absolute directory containing this `SKILL.md`. Put task code in a single-quoted Bash here-document and run it through `$SKILL_DIR/scripts/windows_command.py`. The runner sends transparent source on stdin to a fixed, runner-owned bootstrap, parses the complete script before executing it, preserves native exit status, and emits a result envelope.
6. Before a non-trivial Windows batch, or after any interop error, run `scripts/check_windows_interop.py` with the verified expected host. It checks binfmt, `$WSL_INTEROP`, bounded cmd/PowerShell probes, and recent relay logs without changing state.
7. Classify any failure before retrying. Use `scripts/classify-windows-command-failure.sh` and [references/failure-ledger.md](references/failure-ledger.md). Do not retry a transport failure as a syntax change.
8. Verify the actual Windows state. A zero exit code is evidence, not proof of the requested result.

## WSL interop health

```bash
SKILL_DIR='<absolute path to the directory containing this SKILL.md>'
WINDOWS_EXPECT_HOST='<verified Windows computer name>'
python3 "$SKILL_DIR/scripts/check_windows_interop.py" \
  --expect-host "$WINDOWS_EXPECT_HOST"
```

The health checker is read-only and returns a machine-readable classification:

- `HEALTHY` (exit `0`): registration, both probes, and recent relay logs are clean.
- `TRANSIENT_INTEROP_WARNING` (exit `2`): bounded probes pass but recent vsock/Relay evidence remains. Record it and continue with serialized commands; a warning alone does not justify restart.
- `USABLE_LOGS_UNVERIFIED` or `STATIC_ONLY` (exit `3`): do not claim full health until the missing proof runs.
- `BROKEN_*` (exit `1`): stop retries and use the named owner-layer action.

Never kill a `Relay` PID based only on age, rewrite valid PowerShell after a vsock failure, or call `wsl --shutdown`/`--terminate` from the active WSL session. Those commands can destroy the agent's own proof surface. Read [references/quoting-transport-and-output.md](references/quoting-transport-and-output.md) for the safe recovery boundary.

## PowerShell quick start

Run a read-only local command:

```bash
python3 "$SKILL_DIR/scripts/windows_command.py" \
  powershell --intent read --expect-host "$WINDOWS_EXPECT_HOST" <<'POWERSHELL'
Get-Process |
  Select-Object -First 5 Name, Id, CPU |
  ConvertTo-Json -Compress
POWERSHELL
```

For a Windows path with spaces, pass it as PowerShell data and use `-LiteralPath`:

```bash
python3 "$SKILL_DIR/scripts/windows_command.py" \
  powershell --intent read --expect-host "$WINDOWS_EXPECT_HOST" <<'POWERSHELL'
$path = 'C:\Program Files'
Get-Item -LiteralPath $path | Select-Object FullName, Attributes
POWERSHELL
```

For a write, require both host identity and an existing backup receipt:

```bash
python3 "$SKILL_DIR/scripts/windows_command.py" \
  powershell --intent write --expect-host "$WINDOWS_EXPECT_HOST" \
  --backup-receipt /absolute/path/to/pre-change-manifest.md <<'POWERSHELL'
# Use exact literal targets and add a post-state proof in the calling workflow.
POWERSHELL
```

The runner deliberately rejects several common unsafe patterns, including opaque command transport, execution-policy bypasses, expression evaluation, antivirus weakening, wildcard deletion, and write-like commands submitted as `--intent read`. This is an advisory guard, not a complete PowerShell policy engine. The backup-receipt flag is an existence interlock; the caller remains responsible for verifying that the receipt is current, correct, and covers the intended target.

## Cmd quick start

Use cmd mode only when cmd syntax is required. The runner writes an exact temporary batch file on Windows, invokes it with AutoRun disabled, captures `%ERRORLEVEL%`, and deletes the exact file.

```bash
python3 "$SKILL_DIR/scripts/windows_command.py" \
  cmd --intent read --expect-host "$WINDOWS_EXPECT_HOST" <<'CMD'
@echo off
ver
where.exe powershell.exe
exit /b 0
CMD
```

Prefer PowerShell for Unicode and structured data. Never build a dynamic `cmd.exe /c "..."` string containing `&`, `|`, `<`, `>`, `^`, `%`, `!`, or parentheses.

## Native executables

Invoke ordinary `.exe` programs directly with an argument array through the execution tool or Python `subprocess` with `shell=False`. Do not route them through cmd or PowerShell merely to launch them. If the executable is a `.bat` or `.cmd`, use cmd mode because batch parsing is the language.

After a native command in PowerShell, inspect `$LASTEXITCODE`; `$?` and stderr text are not substitutes. The runner maps a final native nonzero code to the process result.

## Data, output, and paths

- Keep code out of Bash double-quoted strings. Use `<<'POWERSHELL'` or a `.ps1` file.
- Never use Bash's `\` as a PowerShell line continuation. Break after a PowerShell pipe/operator or use balanced parentheses; use a backtick only when unavoidable and never leave spaces after it.
- Keep dynamic data separate from code. Use `--input-json /absolute/path/data.json`; the runner decodes it explicitly as UTF-8 and exposes the parsed object as `$CodexInputData`.
- Use `ConvertTo-Json -Compress -Depth <n>` as the cross-shell structured-output boundary.
- Use `-LiteralPath` for user-controlled or exact paths. Use wildcard-aware parameters only when wildcard behavior is intentional and previewed.
- Resolve WSL paths with `wslpath -w`; never assume a local `C:\...` path names the same machine over SSH.
- Avoid `Format-Table` before machine consumption. Select objects first and format only at the human display boundary.

Read [references/quoting-transport-and-output.md](references/quoting-transport-and-output.md) for the parser layers, PowerShell 5.1 versus 7 differences, encoding, streams, timeouts, and remote transport rules.

## Failure and safety rules

- Never use encoded or obfuscated command transport, execution-policy bypasses, expression evaluation, download-and-run pipelines, password fallback, broad antivirus exclusions, or security-tool disabling.
- Stop immediately on an antivirus or endpoint-security alert. Inspect the exact readable source and change the method or pause; never bypass the alert.
- Two syntax/transport attempts at the same outcome trigger stop-and-research. Do not keep changing quotes.
- `UtilAcceptVsockAnyPort`, `UtilBindVsockAnyPort`, socket timeout, or executable-dispatch errors are WSL interop failures, not PowerShell syntax failures. Retry one health probe at most, then stop. Do not run `wsl --shutdown` from the active WSL session because it terminates the agent.
- Parser errors must fail before any payload statement runs. A missing result envelope is failure even if the child process reports zero.
- Do not use `exit` inside runner payloads; use `return` for success or `throw` for failure so the runner can emit its final envelope.
- Never auto-elevate, synthesize credentials, or bypass UAC. Return the named approval/visible-proof gap.
- Never kill by broad process name when identical processes may exist. Track exact PID and start time.
- Never perform destructive disk, firewall, security, service, registry, package, or file operations without explicit scope, expected host, backup, literal target, preview, and post-state proof.

Read [references/safety-and-remote-windows.md](references/safety-and-remote-windows.md) before SSH, elevation, installers, services, firewall, security software, disks, or destructive actions.

## Validation

Run static tests from the skill directory:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/test_windows_command.py"
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/test_interop_health.py"
bash -n "$SKILL_DIR/scripts/classify-windows-command-failure.sh"
```

Run real Windows integration tests only while WSL interop is healthy:

```bash
WINDOWS_RUNNER_INTEGRATION=1 \
WINDOWS_EXPECT_HOST="$WINDOWS_EXPECT_HOST" \
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/test_windows_command.py"

WINDOWS_INTEROP_HEALTH_INTEGRATION=1 \
WINDOWS_EXPECT_HOST="$WINDOWS_EXPECT_HOST" \
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/test_interop_health.py"
```

If integration reports an interop transport failure, do not rewrite PowerShell source. Preserve the receipt and recover the WSL bridge from Windows at a safe session boundary.
