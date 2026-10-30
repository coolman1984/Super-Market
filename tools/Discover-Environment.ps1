# Read-only environment inventory. No installation or opening customer documents.
$ErrorActionPreference = 'Continue'
$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
$cpu = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Select-Object -First 1
$python = Get-Command python,py -ErrorAction SilentlyContinue | Select-Object Name,Source
$office = @{}
foreach ($item in @('Excel.Application','Word.Application','PowerPoint.Application')) {
    try { $office[$item] = [bool][type]::GetTypeFromProgID($item) } catch { $office[$item] = $false }
}
$nasca = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match 'nasca|drm' } | Select-Object -ExpandProperty ProcessName)
$result = [ordered]@{
    captured_at = (Get-Date).ToString('o')
    os = $os.Caption
    os_architecture = $os.OSArchitecture
    ram_gb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
    cpu = $cpu.Name
    logical_processors = $cpu.NumberOfLogicalProcessors
    powershell = $PSVersionTable.PSVersion.ToString()
    python_commands = @($python)
    office_com_registered = $office
    possible_drm_processes = $nasca
    drm_detection_note = 'Process names and COM registration are hints only. Confirm with an authorized sample and the actual application.'
    data_directory_writable = $null
    network_printers_scales = 'Confirm exact hardware and network configuration before enabling adapters.'
    packages_installed = 'Not assumed. Core must work without external packages.'
}
$result | ConvertTo-Json -Depth 5
