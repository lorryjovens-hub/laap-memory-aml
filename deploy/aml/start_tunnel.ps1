# 启动 Cloudflare 隧道
# 用法:
#   .\start_tunnel.ps1                      # 前台
#   .\start_tunnel.ps1 -Background          # 后台（日志写 ~/.laap/cloudflared.log）
#   .\start_tunnel.ps1 -TunnelName laap-aml
param(
    [string]$TunnelName = "laap-aml",
    [switch]$Background
)
$ErrorActionPreference = "Stop"

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    throw "cloudflared 未安装，请先运行 install_cloudflared.ps1"
}

$cfg = Join-Path $PSScriptRoot "cloudflared_config.yml"
if (-not (Test-Path $cfg)) { throw "缺少配置: $cfg" }
if ((Get-Content $cfg -Raw) -match "<TUNNEL_ID>") {
    throw "配置里仍是占位符 <TUNNEL_ID>，请先运行 setup_tunnel.ps1"
}

if ($Background) {
    $log = Join-Path $env:USERPROFILE ".laap\cloudflared.log"
    $err = Join-Path $env:USERPROFILE ".laap\cloudflared.err.log"
    $p = Start-Process -FilePath "cloudflared" `
        -ArgumentList @("--config", $cfg, "tunnel", "run", $TunnelName) `
        -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError $err
    Write-Host "隧道已后台启动 (PID $($p.Id))"
    Write-Host "  日志: $log / $err"
    Write-Host "  查看: cloudflared tunnel info $TunnelName"
} else {
    & cloudflared --config $cfg tunnel run $TunnelName
}
