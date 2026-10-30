# Read a (possibly NASCA/DRM-protected) Excel file THROUGH the authorized Excel
# application, read-only, and write its used range to CSV for preview/import.
# - Starts its OWN hidden Excel instance in a separate job with a timeout.
# - ReadOnly, UpdateLinks=0, macros disabled (AutomationSecurity = ForceDisable).
# - Closes only the workbook and the Excel process it created (tracked by PID);
#   never kills other Excel windows and never touches the user's open files.
# - Never copies the raw document anywhere.
# Not yet verified on a Windows PC with NASCA (see ENVIRONMENT_REPORT.md).
#   powershell -NoProfile -File tools\Read-ExcelViaCom.ps1 -Path C:\x\items.xlsx -Out C:\x\items.csv [-Sheet 1] [-MaxRows 20000] [-TimeoutSec 120]
param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [string]$Out,
      [int]$Sheet = 1, [int]$MaxRows = 20000, [int]$MaxCols = 60, [int]$TimeoutSec = 120)

$job = Start-Job -ArgumentList $Path, $Out, $Sheet, $MaxRows, $MaxCols -ScriptBlock {
    param($Path, $Out, $Sheet, $MaxRows, $MaxCols)
    $before = @(Get-Process EXCEL -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    $excel = New-Object -ComObject Excel.Application
    $mine = @(Get-Process EXCEL -ErrorAction SilentlyContinue | Where-Object { $before -notcontains $_.Id } | Select-Object -ExpandProperty Id)
    $wb = $null
    try {
        $excel.Visible = $false; $excel.DisplayAlerts = $false; $excel.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        $wb = $excel.Workbooks.Open($Path, 0, $true)                                          # UpdateLinks=0, ReadOnly=true
        $ws = $wb.Worksheets.Item($Sheet)
        $data = $ws.UsedRange.Value2
        $rows = [math]::Min($ws.UsedRange.Rows.Count, $MaxRows); $cols = [math]::Min($ws.UsedRange.Columns.Count, $MaxCols)
        $lines = New-Object System.Collections.Generic.List[string]
        for ($r = 1; $r -le $rows; $r++) {
            $cells = for ($c = 1; $c -le $cols; $c++) {
                $v = if ($rows -eq 1 -and $cols -eq 1) { $data } else { $data[$r, $c] }
                $s = if ($null -eq $v) { '' } else { [string]$v }
                if ($s -match '^[=+\-@]') { $s = "'" + $s }                                  # formula-injection guard
                '"' + $s.Replace('"', '""') + '"'
            }
            $lines.Add(($cells -join ','))
        }
        [IO.File]::WriteAllLines($Out, $lines, (New-Object Text.UTF8Encoding $true))
        "ok rows=$rows cols=$cols"
    } finally {
        if ($wb) { $wb.Close($false) | Out-Null; [Runtime.InteropServices.Marshal]::ReleaseComObject($wb) | Out-Null }
        $excel.Quit(); [Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
        Start-Sleep -Milliseconds 500
        foreach ($id in $mine) { Get-Process -Id $id -ErrorAction SilentlyContinue | Stop-Process -ErrorAction SilentlyContinue }
    }
}
if (Wait-Job $job -Timeout $TimeoutSec) { Receive-Job $job; Remove-Job $job }
else {
    Stop-Job $job; Remove-Job $job -Force
    Write-Error "timeout after $TimeoutSec s: Excel did not answer (protection prompt, licence or busy). Use an authorized CSV export instead."
    exit 2
}
