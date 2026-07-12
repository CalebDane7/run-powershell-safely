# PowerShell Command Map

Use this map to find the right owner command. Do not treat it as an exhaustive catalog; installed modules and Windows versions differ.

## Discover before guessing

```powershell
Get-Command -Name Get-Process -Syntax
Get-Command -Verb Get -Noun '*Service*'
Get-Help Get-ChildItem -Full
Get-Help Get-ChildItem -Examples
$object | Get-Member
Get-Module -ListAvailable | Sort-Object Name, Version
```

Use `Get-Help <cmdlet> -Online` only when current web documentation is needed. Probe `Get-Command <name>` before relying on a remembered cmdlet.

## Select the correct data shape

```powershell
$items | Where-Object Status -eq 'Running'
$items | Sort-Object Name
$items | Select-Object Name, Status, @{n='Age';e={...}}
$items | Group-Object Status
$items | Measure-Object Length -Sum
$items | ConvertTo-Json -Compress -Depth 6
```

Collect objects before formatting. `Format-Table` and `Format-List` create formatting records and should be the final human-only step.

## Files and paths

Read:

```powershell
Test-Path -LiteralPath $path
Get-Item -LiteralPath $path -Force
Get-ChildItem -LiteralPath $path -Force
Get-Content -LiteralPath $path -Raw
Get-FileHash -LiteralPath $path -Algorithm SHA256
Resolve-Path -LiteralPath $path
Split-Path -LiteralPath $path -Parent
Join-Path $parent $child
```

Write-gated commands include `New-Item`, `Copy-Item`, `Move-Item`, `Rename-Item`, `Remove-Item`, `Set-Content`, `Add-Content`, and `Clear-Content`. Use exact `-LiteralPath`, back up first, and verify post-state. Never use wildcard deletion.

## Desktop, Start Menu, shortcuts, and startup entries

Discover the actual per-user and shared folders instead of guessing localized paths:

```powershell
$desktop = [Environment]::GetFolderPath('Desktop')
$commonDesktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
$startMenu = [Environment]::GetFolderPath('StartMenu')
$commonStartMenu = [Environment]::GetFolderPath('CommonStartMenu')
Get-ChildItem -LiteralPath $desktop,$commonDesktop -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_StartupCommand | Select-Object Name, Command, Location, User
```

Resolve a shortcut without launching it:

```powershell
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
[pscustomobject]@{ Path=$lnkPath; Target=$shortcut.TargetPath; Arguments=$shortcut.Arguments; WorkingDirectory=$shortcut.WorkingDirectory }
```

Deleting a `.lnk` removes only that exact shortcut, not the installed program, but it is still a write: list and resolve first, back up the exact file, use `-LiteralPath`, and verify the program remains discoverable from the Start Menu or uninstall registry.

## Processes and applications

```powershell
Get-Process
Get-Process -Id $pid
Get-CimInstance Win32_Process | Select-Object ProcessId, Name, ExecutablePath, CommandLine
Start-Process -FilePath $exe -ArgumentList $args -PassThru
Wait-Process -Id $pid -Timeout 30
Stop-Process -Id $pid -WhatIf
```

`Start-Process -ArgumentList` joins its array into one command-line string. It is not a universal escape-free argument API. Track exact PID and start time; avoid broad name-based termination.

## Services and scheduled tasks

```powershell
Get-Service
Get-CimInstance Win32_Service | Select-Object Name, State, StartMode, PathName
Get-ScheduledTask
Get-ScheduledTaskInfo -TaskName $name
```

`Start-Service`, `Stop-Service`, `Restart-Service`, `Set-Service`, task registration, and task removal are write/elevation gated.

## System, hardware, drivers, and Windows version

```powershell
Get-ComputerInfo
Get-CimInstance Win32_OperatingSystem
Get-CimInstance Win32_ComputerSystem
Get-CimInstance Win32_BIOS
Get-CimInstance Win32_Processor
Get-CimInstance Win32_LogicalDisk
Get-PnpDevice
Get-CimClass -ClassName 'Win32_*' | Select-Object CimClassName
```

Prefer CIM cmdlets over deprecated WMI cmdlets when both are available.

## Installed applications and packages

```powershell
$roots = @(
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
Get-ItemProperty $roots -ErrorAction SilentlyContinue |
  Where-Object DisplayName |
  Select-Object DisplayName, DisplayVersion, Publisher, UninstallString

winget list --accept-source-agreements
Get-AppxPackage
Get-Package
```

Use the vendor-owned uninstaller when it controls data-retention or product state. `winget` and registry entries are discovery evidence, not permission to uninstall an ambiguous match.

## Registry

```powershell
Test-Path -LiteralPath 'HKLM:\Software\...'
Get-Item -LiteralPath 'HKLM:\Software\...'
Get-ItemProperty -LiteralPath 'HKLM:\Software\...'
Get-ChildItem -LiteralPath 'HKLM:\Software\...'
```

`New-Item`, `Set-ItemProperty`, `New-ItemProperty`, and removal cmdlets are write gated. Export or record the exact prior values first.

## Networking and firewall

```powershell
Get-NetIPAddress
Get-NetAdapter
Get-NetRoute
Get-NetTCPConnection
Get-DnsClientServerAddress
Resolve-DnsName $name
Test-NetConnection $host -Port $port
Get-NetFirewallProfile
Get-NetFirewallRule
Get-NetFirewallPortFilter
```

Firewall changes are protected writes. Do not add broad rules or disable profiles to make a test pass.

## Event logs and diagnostics

```powershell
Get-WinEvent -ListLog *
Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddHours(-1)} -MaxEvents 100
Get-Counter
Get-EventLog -LogName System -Newest 50
```

Use `Get-WinEvent` for current structured event queries. Filter early to avoid enormous output.

## Updates, security status, and integrity inventory

Read-only inventory varies by edition and installed modules:

```powershell
Get-HotFix
Get-CimInstance Win32_QuickFixEngineering
Get-MpComputerStatus
Get-MpThreatDetection
Get-Tpm
Get-BitLockerVolume
Get-AuthenticodeSignature -LiteralPath $path
```

Some security cmdlets require elevation or are unavailable when a third-party endpoint-security product owns the surface. Absence or access denial is not permission to weaken protection. Never enumerate secret-bearing preferences into logs, change antivirus or endpoint-security settings, alter execution policy, or disable firewall/security controls to make automation work.

## Users, groups, identity, and ACLs

```powershell
[Environment]::UserName
[Environment]::MachineName
whoami.exe
Get-LocalUser
Get-LocalGroup
Get-LocalGroupMember -Group Administrators
Get-Acl -LiteralPath $path
```

Account, group, ownership, and ACL changes are protected writes. Never print keys, tokens, credential objects, or secret-bearing environment variables.

## Shares, printers, locale, time, and power

```powershell
Get-SmbShare
Get-SmbSession
Get-Printer
Get-PrintJob -PrinterName $printer
Get-Culture
Get-UICulture
Get-TimeZone
Get-CimInstance Win32_Battery
powercfg.exe /getactivescheme
```

Adding/removing shares or printers, cancelling print jobs, changing locale/time, and changing power plans are writes. Query selected environment variables such as `$env:SystemRoot` by exact name; do not dump the entire environment because it may contain credentials or tokens.

## Disks, partitions, and volumes

Read-only inventory:

```powershell
Get-Disk | Select-Object Number,FriendlyName,SerialNumber,BusType,Size,PartitionStyle,IsBoot,IsSystem,IsReadOnly,OperationalStatus
Get-Partition | Select-Object DiskNumber,PartitionNumber,DriveLetter,Type,Size,Offset
Get-Volume | Select-Object DriveLetter,FileSystemLabel,FileSystem,DriveType,Size,SizeRemaining,Path
```

`Clear-Disk`, `Initialize-Disk`, partition changes, and formatting are destructive, elevation-gated operations. Require current identity from disk and volume views, reject boot/system disks, use a disposable proof target, and obtain real visible UAC approval when needed.

## Windows capabilities and features

```powershell
Get-WindowsOptionalFeature -Online
Get-WindowsCapability -Online
Get-WindowsFeature
```

Availability differs between Windows client and Windows Server. Probe the command first. Enable/disable/install/remove operations are write and usually elevation gated.

## Certificates and signatures

```powershell
Get-AuthenticodeSignature -LiteralPath $path
Get-ChildItem Cert:\CurrentUser\My
Get-ChildItem Cert:\LocalMachine\My
```

Do not install trust roots, weaken signature policy, or execute an unsigned download without explicit scope and verification.

## JSON, CSV, text, and encoding

```powershell
$json | ConvertFrom-Json
$object | ConvertTo-Json -Depth 6 -Compress
Import-Csv -LiteralPath $path
$rows | Export-Csv -LiteralPath $path -NoTypeInformation -Encoding UTF8
Select-String -LiteralPath $path -Pattern $pattern
```

Windows PowerShell 5.1 encoding defaults differ from PowerShell 7. Use explicit encoding and test non-ASCII round trips.

## Cmd and native compatibility tools

Common native tools include `where.exe`, `whoami.exe`, `ipconfig.exe`, `netstat.exe`, `reg.exe`, `sc.exe`, `schtasks.exe`, `robocopy.exe`, `msiexec.exe`, and `winget.exe`. Prefer object-producing PowerShell cmdlets when available. Use cmd mode for cmd built-ins such as `ver`, `set`, `dir` batch semantics, and `%ERRORLEVEL%`.
