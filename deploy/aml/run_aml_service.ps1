# 启动 AML Add/Search 记忆服务
# 用法:
#   .\run_aml_service.ps1                 # 前台
#   .\run_aml_service.ps1 -Background     # 后台（日志写 ~/.laap/aml_service.log）
#   .\run_aml_service.ps1 -Port 8095 -Db "$HOME\.laap\aml_memory.sqlite3"
param(
    [int]$Port = 8095,
    [string]$Host_ = "127.0.0.1",
    [string]$Db = "",
    [switch]$Background,
    [switch]$Quiet
)
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

if (-not $Db) {
    $Db = Join-Path $env:USERPROFILE ".laap\aml_memory.sqlite3"
}
New-Item -ItemType Directory -Force -Path (Split-Path $Db) | Out-Null

if (-not $env:AML_MEMORY_KEY) {
    Write-Warning "AML_MEMORY_KEY 未设置 —— 服务将对外开放（仅适合本机调试）"
    Write-Warning "生产请先: `$env:AML_MEMORY_KEY = '<强随机串>'"
}

$argsList = @("-m", "laap.aml.server", "--host", $Host_, "--port", "$Port", "--db", $Db)

Write-Host "启动 AML 记忆服务 http://${Host_}:${Port}"
Write-Host "  数据库: $Db"
Write-Host "  鉴权:   $(if ($env:AML_MEMORY_KEY) { '开启' } else { '关闭' })"

Push-Location $RepoRoot
try {
    if ($Background) {
        $log = Join-Path $env:USERPROFILE ".laap\aml_service.log"
        $err = Join-Path $env:USERPROFILE ".laap\aml_service.err.log"
        $p = Start-Process -FilePath $Py -ArgumentList $argsList -PassThru -NoNewWindow `
            -RedirectStandardOutput $log -RedirectStandardError $err
        Write-Host "已后台启动 (PID $($p.Id))  日志: $log"
        Start-Sleep -Seconds 5
        try {
            $h = Invoke-RestMethod -Uri "http://${Host_}:${Port}/health" -TimeoutSec 5
            Write-Host "健康检查: $($h | ConvertTo-Json -Compress)"
        } catch {
            Write-Warning "健康检查失败: $($_.Exception.Message)（看 $err）"
        }
    } else {
        & $Py @argsList
    }
} finally {
    Pop-Location
}
