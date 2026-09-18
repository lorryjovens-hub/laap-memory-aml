# 注册开机自启（记忆服务 + Cloudflare 隧道）
#
# 用「当前用户启动文件夹」实现 —— 免管理员权限。
# 若以管理员运行，可加 -UseScheduledTask 改用计划任务（更稳，进程受管）。
#
# 用法:
#   .\install_autostart.ps1                    # 启动文件夹（免提权）
#   .\install_autostart.ps1 -UseScheduledTask  # 计划任务（需管理员）
#   .\install_autostart.ps1 -Remove            # 卸载
param(
    [string]$TunnelName = "laap-aml",
    [switch]$UseScheduledTask,
    [switch]$Remove
)
$ErrorActionPreference = "Stop"

$SvcTask = "LAAP-AML-Memory"
$TunTask = "LAAP-AML-Tunnel"
$Startup = [Environment]::GetFolderPath("Startup")
$LauncherName = "laap-aml-autostart.cmd"
$Launcher = Join-Path $Startup $LauncherName

# ── 卸载 ────────────────────────────────────────────────────────────────
if ($Remove) {
    if (Test-Path $Launcher) { Remove-Item $Launcher -Force; Write-Host "已移除启动项: $Launcher" }
    foreach ($t in @($SvcTask, $TunTask)) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
            Write-Host "已移除计划任务: $t"
        }
    }
    exit 0
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }
$Db = Join-Path $env:USERPROFILE ".laap\aml_memory.sqlite3"
$Cfg = Join-Path $PSScriptRoot "cloudflared_config.yml"
$LogDir = Join-Path $env:USERPROFILE ".laap"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if ((Get-Content $Cfg -Raw) -match "<TUNNEL_ID>") {
    Write-Warning "隧道配置仍是占位符 <TUNNEL_ID>；请先运行 setup_tunnel.ps1。"
}

$cfExe = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cfExe) {
    $candidate = Join-Path $env:LOCALAPPDATA "cloudflared\cloudflared.exe"
    if (Test-Path $candidate) { $cfExe = $candidate }
}

# ══════════════════════════════════════════════════════════════════════
# 方案 A：启动文件夹（默认，免提权）
# ══════════════════════════════════════════════════════════════════════
if (-not $UseScheduledTask) {
    $lines = @(
        "@echo off",
        "rem LAAP AML 自启（由 install_autostart.ps1 生成）",
        "setlocal",
        "if defined AML_MEMORY_KEY (echo [OK] AML_MEMORY_KEY present) else (echo [WARN] AML_MEMORY_KEY not set)",
        "start `"`" /min `"$Py`" -m laap.aml.server --host 127.0.0.1 --port 8095 --db `"$Db`" >> `"$LogDir\aml_service.log`" 2>>&1",
        "timeout /t 5 /nobreak >nul",
        "start `"`" /min `"$cfExe`" --config `"$Cfg`" tunnel run $TunnelName >> `"$LogDir\cloudflared.log`" 2>>&1",
        "endlocal"
    )
    # 先写到部署目录（一定可写），再尝试拷入启动文件夹
    $staged = Join-Path $PSScriptRoot $LauncherName
    $lines -join "`r`n" | Set-Content -Path $staged -Encoding OEM
    Write-Host "已生成启动脚本: $staged"

    try {
        Copy-Item $staged $Launcher -Force -ErrorAction Stop
        Write-Host "已注册到启动文件夹: $Launcher"
        Write-Host "（登录 Windows 时自动拉起记忆服务与隧道）"
    } catch {
        Write-Warning "无法写入启动文件夹（需手动执行一次）:"
        Write-Host ""
        Write-Host "  请手动运行下面这条命令完成注册："
        Write-Host "  Copy-Item `"$staged`" `"$Launcher`" -Force"
        Write-Host ""
        Write-Host "  或直接在文件资源管理器地址栏输入 shell:startup 然后把该文件拖进去。"
    }
    Write-Host ""
    Write-Host "立即启动（无需重启）:"
    Write-Host "  & `"$staged`""
    exit 0
}

# ══════════════════════════════════════════════════════════════════════
# 方案 B：计划任务（需管理员）
# ══════════════════════════════════════════════════════════════════════
Write-Host "使用计划任务方案（需管理员权限）..."
$svcArgs = "-m laap.aml.server --host 127.0.0.1 --port 8095 --db `"$Db`""
$svcAction = New-ScheduledTaskAction -Execute $Py -Argument $svcArgs -WorkingDirectory $RepoRoot
$svcTrigger = New-ScheduledTaskTrigger -AtLogOn
$svcSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $SvcTask -Action $svcAction -Trigger $svcTrigger `
    -Settings $svcSettings -Force | Out-Null
Write-Host "已注册: $SvcTask"

if ($cfExe) {
    $tunAction = New-ScheduledTaskAction -Execute $cfExe `
        -Argument "--config `"$Cfg`" tunnel run $TunnelName"
    $tunTrigger = New-ScheduledTaskTrigger -AtLogOn
    $tunSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $TunTask -Action $tunAction -Trigger $tunTrigger `
        -Settings $tunSettings -Force | Out-Null
    Write-Host "已注册: $TunTask"
}
Write-Host ""
Write-Host "立即启动:"
Write-Host "  Start-ScheduledTask -TaskName $SvcTask"
Write-Host "  Start-ScheduledTask -TaskName $TunTask"
