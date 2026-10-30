# Read-only environment inventory for the shop PC (Windows PowerShell 5.1+).
# Installs nothing, opens no customer documents, changes no device settings.
# The only write is a tiny probe file in the program folder (created + deleted)
# to prove the data folder is writable. Output: ENVIRONMENT_REPORT.local.json
# (git-ignored). Computer and user names are NOT recorded.
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\Discover-Environment.ps1
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
function Try-Run($block) { try { & $block } catch { "error: $($_.Exception.Message)" } }

$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
$cpu = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Select-Object -First 1
$disk = Try-Run { Get-PSDrive -Name ($root.Substring(0,1)) | Select-Object @{n='free_gb';e={[math]::Round($_.Free/1GB,1)}} }

# Python candidates and their sqlite version (standard library only)
$py = @()
foreach ($cand in @("$root\python\python.exe", 'py', 'python')) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) {
        $pyArgs = if ($cand -eq 'py') { @('-3', '-c') } else { @('-c') }
        $ver = Try-Run { & $cmd.Source @pyArgs "import sys,sqlite3,ssl;print(sys.version.split()[0], sqlite3.sqlite_version)" 2>$null }
        $py += [ordered]@{ command = $cand; version = "$ver" }
    }
}

# Writable data folder probe
$probe = Join-Path $root ('.write-probe-' + [guid]::NewGuid().ToString('N'))
$writable = $false
try { Set-Content -Path $probe -Value 'x' -ErrorAction Stop; Remove-Item $probe -ErrorAction Stop; $writable = $true } catch { $writable = $false }

# Office COM registration (a hint only; real proof is opening an authorized sample)
$office = [ordered]@{}
foreach ($item in @('Excel.Application', 'Word.Application', 'PowerPoint.Application')) {
    $office[$item] = [bool]([type]::GetTypeFromProgID($item))
}
$drm = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match 'nasca|drm|dlp' } | Select-Object -ExpandProperty ProcessName -Unique)

# Printers: name, driver, port (receipt printers often show as POS-80, XP-80, TM-T20 ...)
$printers = @(Try-Run { Get-CimInstance Win32_Printer | Select-Object Name, DriverName, PortName, Default, WorkOffline })

# Serial ports: real COM, USB virtual COM (CDC/VCP) and Bluetooth SPP ports
$serial = @(Try-Run { Get-CimInstance Win32_SerialPort | Select-Object DeviceID, Description, PNPDeviceID })
$serialMap = Try-Run { Get-ItemProperty 'HKLM:\HARDWARE\DEVICEMAP\SERIALCOMM' | Select-Object * -ExcludeProperty PS* }

# HID / keyboard-type devices whose names suggest a scanner or scale (hint only)
$pattern = 'scan|barcode|honeywell|zebra|symbol|datalogic|newland|sunmi|netum|inateck|eyoyo|xprinter|rongta|syble|zebex|argox|posiflex|scale|cas |aclas|dibal|mettler|bluetooth'
$devices = @(Try-Run { Get-PnpDevice -PresentOnly | Where-Object { $_.FriendlyName -match $pattern -or $_.Class -in @('Ports', 'Bluetooth') } |
    Select-Object Class, FriendlyName, Status, @{n='vid_pid';e={ if ($_.InstanceId -match 'VID_([0-9A-F]{4})&PID_([0-9A-F]{4})') { "$($matches[1]):$($matches[2])" } }} })
$keyboards = @(Try-Run { Get-CimInstance Win32_Keyboard | Select-Object Description, Layout })
$languages = @(Try-Run { Get-WinUserLanguageList | ForEach-Object { [ordered]@{ tag = $_.LanguageTag; inputs = ($_.InputMethodTips -join ',') } } })

$firewall = @(Try-Run { Get-NetFirewallProfile | Select-Object Name, Enabled })
$ips = @(Try-Run { Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' } | Select-Object InterfaceAlias, IPAddress, PrefixLength })
$tz = Try-Run { (Get-TimeZone).Id }
$admin = Try-Run { ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) }

$result = [ordered]@{
    captured_at = (Get-Date).ToString('o')
    os = $os.Caption; os_version = $os.Version; os_architecture = $os.OSArchitecture
    ram_gb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
    cpu = $cpu.Name; logical_processors = $cpu.NumberOfLogicalProcessors
    disk_free = $disk
    powershell = $PSVersionTable.PSVersion.ToString()
    running_as_admin = $admin
    python = $py
    program_folder_writable = $writable
    timezone = $tz
    office_com_registered = $office
    possible_drm_processes = $drm
    printers = $printers
    serial_ports = $serial; serial_map = $serialMap
    scanner_scale_bluetooth_devices = $devices
    keyboards = $keyboards; input_languages = $languages
    firewall = $firewall; ipv4 = $ips
    notes = @(
        'COM registration and process names are hints. Prove Office/NASCA access by opening an authorized sample through the application.',
        'A keyboard-type scanner is invisible here unless its name says so: test it in the Scanner screen.',
        'Timezone should be Egypt Standard Time; expiry and shift dates use the PC clock.',
        'No package installation or internet access is attempted.'
    )
}
$out = Join-Path $root 'ENVIRONMENT_REPORT.local.json'
$result | ConvertTo-Json -Depth 6 | Set-Content -Path $out -Encoding UTF8
Write-Output "Saved $out"
