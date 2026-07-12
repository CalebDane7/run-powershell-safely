# Safety And Remote Windows

## Standing SSH rule

Set a pinned SSH target and expected identity supplied by the task or connection owner. Use key authentication and separate shell-neutral identity probes:

```bash
WINDOWS_SSH_TARGET='<user@host or pinned SSH alias>'
WINDOWS_EXPECT_HOST='<verified Windows computer name>'
WINDOWS_EXPECT_USER='<verified Windows user>'

ssh -o BatchMode=yes \
    -o PasswordAuthentication=no \
    -o StrictHostKeyChecking=yes \
    -o ConnectTimeout=10 \
    "$WINDOWS_SSH_TARGET" hostname.exe

ssh -o BatchMode=yes \
    -o PasswordAuthentication=no \
    -o StrictHostKeyChecking=yes \
    -o ConnectTimeout=10 \
    "$WINDOWS_SSH_TARGET" whoami.exe
```

Compare the returned host and user with the caller-supplied expected values and stop on a mismatch. Keep commands plain and readable. Never use opaque command transport, obfuscation, expression evaluation, execution-policy bypass, download-and-run, password fallback, or broad security exclusions.

## Remote execution sequence

1. Run the read-only identity preflight.
2. Determine whether the target command belongs to Windows cmd, Windows PowerShell, or a WSL distro on the verified remote Windows target.
3. For simple read-only native probes, send one fixed literal command.
4. For complex PowerShell, use a fixed bootstrap already proven on that exact remote default shell. If separate stdin or file identity is required, stage a readable random ASCII `.ps1`, execute with `-File`, and remove only that exact name.
5. Never use wildcard cleanup. Report any leftover exact path.
6. Stop immediately on an antivirus or endpoint-security alert.

Do not modify Windows OpenSSH `DefaultShell`, VPN or overlay-network configuration, firewall, credentials, SSH configuration, or endpoint security to make command transport easier.

## Read versus write

Read-only inventory includes identity, version, existence, metadata, process/service state, registry reads, package lists, network connections, event-log reads, and disk/volume inventory.

Writes include file creation/deletion/moves, registry changes, service changes, scheduled tasks, firewall rules, package install/uninstall, disk/partition/format actions, account/ACL changes, security settings, and restarts. For a write:

- name the correct local/remote host;
- create a timestamped backup and manifest before changed bytes;
- use literal targets and preview/count the affected set;
- use normal-user rights unless the vendor flow genuinely requires elevation;
- obtain real visible UAC/user approval when needed;
- verify the real post-state;
- preserve credentials and private data outside logs and argv.

## Antivirus and downloads

Keep endpoint security enabled. Do not disable protection, add broad exclusions, or disguise commands. A detection is evidence to stop and inspect.

When a download is authorized, save it without executing, verify its source, hash, and signature, and treat execution as a separate reviewed step. Never pipe network content into a shell or expression evaluator.

## UAC and visible applications

Sandbox or Linux root access is not Windows Administrator/UAC approval. Do not auto-elevate. If a vendor uninstaller, disk tool, or protected setting requires UAC, open the real signed UI and leave the row waiting for visible approval. CLI/source proof cannot replace the reported desktop-app flow.

## Protected destructive areas

Disk formatting, boot media, firewall, SSH, antivirus, accounts, ACLs, services, registry, uninstallers, and process termination require exact owner evidence. Never carry a disk number, USB serial, process PID, or temporary path from an older session into a new mutation without refreshing it live.
