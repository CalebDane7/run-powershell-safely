# Quoting, Transport, And Output

## Parser layers

A command may cross Bash, WSL interop, Windows process argv construction, Windows OpenSSH's configured default shell, PowerShell parsing, and a native program's own parser. A quote that protects one layer may be removed or reinterpreted by the next.

The production runner avoids dynamic task source in argv. Python launches `powershell.exe` with `shell=False`; argv contains only a fixed bootstrap. Task source is transparent UTF-8 stdin. The bootstrap reads all source, compiles it once with `ScriptBlock.Create`, and executes only after the complete parse succeeds.

## Safe source boundary

Use a single-quoted Bash here-document:

```bash
python3 scripts/windows_command.py powershell --intent read <<'POWERSHELL'
$processes = Get-Process
$processes | Where-Object { $_.CPU -gt 1 }
POWERSHELL
```

The quoted delimiter prevents Bash from expanding `$processes`, `$_`, `$()`, backticks, or backslashes.

Do not use:

```bash
powershell.exe -Command "$p = Get-Process; $p"
```

Bash consumes `$p` before PowerShell starts. Do not repair increasingly complex source by adding backslashes; move it to the runner boundary.

## Parse the whole program

Raw `powershell.exe -Command -` executes stdin statement by statement. A parser error can be printed while the host still returns zero. The runner's fixed bootstrap buffers and parses the complete source first.

Backslash is a normal PowerShell character, not Bash-style line continuation. Prefer a syntactically open construct:

```powershell
$rows = Get-Process |
  Sort-Object WorkingSet64 -Descending |
  Select-Object -First 3
```

Balanced parentheses, arrays, hashtables, and script blocks can also span lines. PowerShell's backtick continuation is fragile because any trailing space breaks it; avoid it unless the grammar offers no clearer option.

PowerShell does not allow piping directly from a `foreach (...) { ... }` statement:

```powershell
$rows = foreach ($item in $items) {
  [pscustomobject]@{ Name = $item.Name }
}
$rows | Sort-Object Name
```

## Code versus data

Do not interpolate dynamic data into source. Place JSON in an exact file and pass `--input-json`; then read:

```powershell
$inputData = $CodexInputData
```

The runner reads `CODEX_WINDOWS_INPUT_PATH` with .NET's explicit UTF-8 decoder and parses it before task execution. Do not fall back to bare `Get-Content` for a UTF-8 file in Windows PowerShell 5.1; its default legacy code page can corrupt non-ASCII JSON.

Use arrays for native arguments inside PowerShell:

```powershell
$exe = 'C:\Program Files\Vendor\tool.exe'
$arguments = @('--name', 'value with spaces', '--count', '2')
& $exe @arguments
if ($LASTEXITCODE -ne 0) { throw "tool failed with $LASTEXITCODE" }
```

## PowerShell 5.1 versus PowerShell 7

Probe `$PSVersionTable` and `Get-Command pwsh.exe,powershell.exe -ErrorAction SilentlyContinue` once. The bundled runner currently targets Windows PowerShell through `powershell.exe`; do not assume PowerShell 7 features such as newer native-argument behavior or `-CommandWithArgs`.

Windows PowerShell 5.1 may interpret UTF-8 script files without a BOM using the legacy code page. The stdin runner explicitly sets console input/output to UTF-8 and explicitly decodes `--input-json` as UTF-8. A future visible `.ps1` fallback must use UTF-8 with BOM.

## Streams and structured output

- stdout is task output.
- stderr carries errors and the runner's final sentinel result line.
- `$LASTEXITCODE` is the last native process status.
- `$?` is PowerShell pipeline success and is not a replacement for `$LASTEXITCODE`.
- `$ErrorActionPreference='Stop'` turns cmdlet errors into terminating exceptions, but does not convert native nonzero exits in Windows PowerShell 5.1.
- Suppress progress for noninteractive automation; otherwise CLIXML/progress can flood output.
- Use `ConvertTo-Json -Compress -Depth 6` for machine-readable results.

A result is not successful when the sentinel envelope is absent, even if the process code is zero.

## Working directories and paths

PowerShell can start in a WSL UNC path such as `\\wsl.localhost\Ubuntu\...`, but cmd and many native programs reject UNC current directories. The runner defaults such sessions to the Windows user's profile. Use `--cwd` with an explicit Windows or WSL path when the task requires another directory.

Use `wslpath -w /mnt/c/...` for local path translation. Translation does not prove a path exists on a remote Windows target; local and remote filesystems are separate.

## Timeouts and process races

Every run has a finite timeout. On timeout, the runner kills its launched bridge process and reports state as unknown; Windows descendants may require exact-PID inspection before retrying. Never kill every process with the same name merely to clear a timeout.

## WSL interop health and recovery boundary

WSL executable interop has distinct layers: exact PE file and Windows-path discovery, Linux `binfmt_misc` dispatch, the `$WSL_INTEROP` server and `/init`, Windows `wslservice`/hvsocket transport, the Windows target process, and finally PowerShell/cmd parsing. Diagnose them in that order. A `UtilAcceptVsock` error is transport evidence, not PowerShell syntax evidence.

Use `check_windows_interop.py --expect-host <HOST>` for the read-only inspection and one bounded serialized probe sequence. If probes pass but recent relay warnings exist, retain `TRANSIENT_INTEROP_WARNING`; do not restart solely from log text. Trip the circuit breaker only when a requested Windows result fails, the runner envelope is missing, or the one fresh probe fails.

The supported recovery ladder is deliberately narrow:

1. Inspect `$WSL_INTEROP`, `/etc/wsl.conf`, global binfmt status, the exact `WSLInterop` entry, `/init`, the exact executable, and its `MZ` bytes without launching Windows.
2. If and only if the existing `WSLInterop` entry explicitly reads `disabled`, an authorized Linux-root repair may re-enable that existing entry with `echo 1`; re-read it and run one probe. Never hand-register a missing handler.
3. For a proved dispatch/transport outage, preserve all work. From a native Windows owner—not from the affected active distro—use targeted `wsl --terminate <ExactDistro>` first.
4. Use Windows-side `wsl --shutdown` only when multiple distros/shared VM evidence or failed targeted recovery proves the broader blast radius is needed. It stops every distro and the WSL 2 VM.
5. If failure persists, collect the official `microsoft/WSL` `hvsocket` log profile from the Windows owner. Inspect logs for usernames/owner data before sharing; never use execution-policy bypass, download-and-run, or antivirus exclusions to run the collector.

Do not restart `WslService`, `LxssManager`, Hyper-V, HNS, firewall, or antivirus as a routine workaround. Do not blindly update or downgrade WSL; compare the installed version with the exact maintained fix first. Current Microsoft references: [troubleshooting guide](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting-guide), [basic commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands), [advanced configuration](https://learn.microsoft.com/en-us/windows/wsl/wsl-config), and [official logging guide](https://github.com/microsoft/WSL/blob/master/CONTRIBUTING.md#collect-wsl-logs-recommended-method).

## Remote Windows

Windows OpenSSH may use cmd, PowerShell, or a configured custom default shell. Probe identity and shell behavior; do not change the server's `DefaultShell` to simplify one command. For complex remote work, use a fixed bootstrap proven against that host or a visible exact temporary `.ps1` transferred with strict SSH and exact cleanup. Never insert task source into the SSH command string.
