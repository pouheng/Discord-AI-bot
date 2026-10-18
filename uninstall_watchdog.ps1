# uninstall_watchdog.ps1 — 移除工作排程器工作 MioBotWatchdog
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

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已移除工作：$TaskName"
} catch {
    Write-Host "移除失敗（可能本來就不存在）：$($_.Exception.Message)"
}
Read-Host "按 Enter 關閉"
