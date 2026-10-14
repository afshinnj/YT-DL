[CmdletBinding()]
param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "YT-DL Background Worker"

if ($Uninstall) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed '$TaskName'."
    } else {
        Write-Host "'$TaskName' is not installed."
    }
    exit 0
}

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Supervisor = Join-Path $Root "browser_integration\supervisor.py"
$PythonLauncher = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source
if (-not $PythonLauncher) {
    throw "Python's windowed launcher (pyw.exe) was not found. Install Python with the launcher, then retry."
}

$Action = New-ScheduledTaskAction `
    -Execute $PythonLauncher `
    -Argument ('-3 "{0}" --stay-alive' -f $Supervisor) `
    -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Description "Keeps the YT-DL download queue worker ready in the background." `
    -Force | Out-Null
Write-Host "Installed '$TaskName'. The background worker will start when you sign in to Windows."
