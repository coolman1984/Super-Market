# Read a (possibly NASCA/DRM-protected) Excel file THROUGH the authorized Excel
# application, read-only, and write a bounded part of one sheet to CSV.
#
# Guarantees (audit S02):
# - Its OWN Excel instance in a separate job; the exact PID is taken from the
#   new instance's window handle (never "Excel processes that appeared meanwhile").
# - ReadOnly, UpdateLinks=0, macros disabled (AutomationSecurity = ForceDisable).
# - Only the bounded range (MaxRows x MaxCols) is read; truncation is reported.
# - Numbers shown with a format (leading zeros of barcodes/phones) are written
#   as Excel displays them (.Text), never as a bare number.
# - Output goes to a temporary file and is renamed only after success.
# - On timeout only OUR verified Excel PID is stopped; the user's Excel
#   windows and files are never touched. The raw document is never copied.
# - Bypassing the protection is out of scope: if Excel cannot open the file,
#   ask for an authorized CSV export.
# Not yet verified on a PC with Office + NASCA (see ENVIRONMENT_REPORT.md).
#   powershell -NoProfile -File tools\Read-ExcelViaCom.ps1 -Path C:\x\items.xlsx -Out C:\x\items.csv [-Sheet 1] [-MaxRows 20000] [-MaxCols 60] [-TimeoutSec 120]
param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [string]$Out,
      [int]$Sheet = 1, [int]$MaxRows = 20000, [int]$MaxCols = 60, [int]$TimeoutSec = 120)

$ErrorActionPreference = 'Stop'
if (-not [type]::GetTypeFromProgID('Excel.Application')) {
    [Console]::Error.WriteLine('Excel is not installed or not registered on this PC. Use an authorized CSV export instead.')
    exit 3
}
$Path = (Resolve-Path -LiteralPath $Path).Path
$partial = "$Out.partial"
$pidFile = [IO.Path]::Combine([IO.Path]::GetTempPath(), 'sm-excel-' + [guid]::NewGuid().ToString('N') + '.pid')
$started = Get-Date

$job = Start-Job -ArgumentList $Path, $partial, $Sheet, $MaxRows, $MaxCols, $pidFile -ScriptBlock {
    param($Path, $Partial, $Sheet, $MaxRows, $MaxCols, $PidFile)
    $ErrorActionPreference = 'Stop'
    Add-Type -Namespace SM -Name Win -MemberDefinition '[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(System.IntPtr hWnd, out uint pid);'
    $excel = New-Object -ComObject Excel.Application
    $ownPid = [uint32]0
    [void][SM.Win]::GetWindowThreadProcessId([IntPtr]$excel.Hwnd, [ref]$ownPid)
    Set-Content -LiteralPath $PidFile -Value $ownPid
    $wb = $null
    try {
        $excel.Visible = $false; $excel.DisplayAlerts = $false
        $excel.AutomationSecurity = 3                                   # msoAutomationSecurityForceDisable
        $wb = $excel.Workbooks.Open($Path, 0, $true)                    # UpdateLinks=0, ReadOnly=true
        $ws = $wb.Worksheets.Item($Sheet)
        $used = $ws.UsedRange
        $rowsTotal = $used.Row + $used.Rows.Count - 1
        $colsTotal = $used.Column + $used.Columns.Count - 1
        $rows = [math]::Min($rowsTotal, $MaxRows); $cols = [math]::Min($colsTotal, $MaxCols)
        $lines = New-Object System.Collections.Generic.List[string]
        if ($rows -ge 1 -and $cols -ge 1) {
            $block = $ws.Range($ws.Cells.Item(1, 1), $ws.Cells.Item($rows, $cols)).Value2   # bounded read only
            for ($r = 1; $r -le $rows; $r++) {
                $cells = for ($c = 1; $c -le $cols; $c++) {
                    $v = if ($rows -eq 1 -and $cols -eq 1) { $block } else { $block[$r, $c] }
                    if ($v -is [double]) { $s = [string]$ws.Cells.Item($r, $c).Text }        # keeps 0012345 as shown
                    elseif ($null -eq $v) { $s = '' } else { $s = [string]$v }
                    if ($s -match '^[=+\-@]') { $s = "'" + $s }                                # formula-injection guard
                    '"' + $s.Replace('"', '""') + '"'
                }
                $lines.Add(($cells -join ','))
            }
        }
        [IO.File]::WriteAllLines($Partial, $lines, (New-Object Text.UTF8Encoding $true))
        [pscustomobject]@{ rows_total = $rowsTotal; cols_total = $colsTotal; rows_written = $rows; cols_written = $cols;
                           truncated = ($rowsTotal -gt $rows -or $colsTotal -gt $cols) } | ConvertTo-Json -Compress
    } finally {
        if ($wb) { $wb.Close($false) | Out-Null; [Runtime.InteropServices.Marshal]::ReleaseComObject($wb) | Out-Null }
        $excel.Quit(); [Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    }
}

function Stop-OwnExcel {
    # stop only the Excel we created: PID from its window, name EXCEL, started after us
    if (-not (Test-Path -LiteralPath $pidFile)) { return }
    $own = [int](Get-Content -LiteralPath $pidFile)
    $p = Get-Process -Id $own -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -eq 'EXCEL' -and $p.StartTime -ge $started.AddSeconds(-2)) {
        Stop-Process -Id $own -ErrorAction SilentlyContinue
    }
}

try {
    if (Wait-Job $job -Timeout $TimeoutSec) {
        $summary = Receive-Job $job -ErrorAction Stop
        Move-Item -LiteralPath $partial -Destination $Out -Force          # only a complete file becomes the result
        Start-Sleep -Seconds 2
        Stop-OwnExcel                                                     # if Excel ignored Quit()
        Write-Output $summary
    } else {
        Stop-Job $job
        Stop-OwnExcel
        [Console]::Error.WriteLine("timeout after $TimeoutSec s: Excel did not answer (protection prompt, licence or busy). Use an authorized CSV export instead.")
        exit 2
    }
} catch {
    Stop-OwnExcel
    [Console]::Error.WriteLine("Excel could not read the file: " + $_.Exception.Message + ". Use an authorized CSV export instead.")
    exit 4
} finally {
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $partial, $pidFile -ErrorAction SilentlyContinue
}
