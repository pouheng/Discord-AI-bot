# install_watchdog.ps1 — 註冊 Windows 工作排程器工作 MioBotWatchdog
#
# 工作每 1 分鐘執行一次 watchdog.py；watchdog.py 會依 watchdog.json 的
# interval_minutes 自行節流。所以調整間隔只要改 json（manager 滑桿即可），
# 不需要重新註冊工作。
#
# 需要系統管理員權限：若非管理員，會自動以 UAC 重新啟動自己。

$ErrorActionPreference = 'Stop'
$TaskName = 'MioBotWatchdog'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host "需要管理員權限，正在開啟 UAC..."
    Start-Process -FilePath 'powershell' -Verb RunAs -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    exit
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Watchdog  = Join-Path $ScriptDir 'watchdog.py'

if (-not (Test-Path -LiteralPath $Watchdog)) {
    Write-Host "找不到 watchdog.py：$Watchdog"
    Read-Host "按 Enter 關閉"
    exit 1
}

# 優先 pythonw.exe（無主控台視窗，避免每分鐘閃一次黑窗）
$py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
if (-not $py) {
    Write-Host "PATH 找不到 pythonw.exe / python.exe，請確認 Python 已加入 PATH。"
    Read-Host "按 Enter 關閉"
    exit 1
}

$action   = New-ScheduledTaskAction -Execute $py -Argument "`"$Watchdog`"" -WorkingDirectory $ScriptDir
$tRepeat  = New-ScheduledTaskTrigger -Once -At (Get-Date) `
                -RepetitionInterval (New-TimeSpan -Minutes 1) `
                -RepetitionDuration ([TimeSpan]::FromDays(3650))
$tLogon   = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries -StartWhenAvailable `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
                -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Force `
    -Action $action -Trigger @($tLogon, $tRepeat) -Settings $settings `
    -Description "澪 bot watchdog：每分鐘檢查 main.py，掛掉就自動重啟（間隔由 watchdog.json 控制）" | Out-Null

Write-Host "已註冊工作：$TaskName"
Write-Host "執行檔 : $py"
Write-Host "腳本   : $Watchdog"
Read-Host "按 Enter 關閉"
