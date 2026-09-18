# 一键创建 Cloudflare 隧道并绑定 aml.laap.cn
# 前置: 已运行 cloudflared tunnel login（会写 ~/.cloudflared/cert.pem）
#
# 用法:
#   .\setup_tunnel.ps1                          # 默认 tunnel=laap-aml, host=aml.laap.cn
#   .\setup_tunnel.ps1 -TunnelName x -Hostname y.laap.cn
param(
    [string]$TunnelName = "laap-aml",
    [string]$Hostname = "aml.laap.cn"
)
$ErrorActionPreference = "Stop"

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    throw "cloudflared 未安装，请先运行 install_cloudflared.ps1"
}

$cert = Join-Path $env:USERPROFILE ".cloudflared\cert.pem"
if (-not (Test-Path $cert)) {
    throw "未登录。请先运行: cloudflared tunnel login"
}

# 1. 创建隧道（已存在则忽略错误并复用）
Write-Host "创建隧道 $TunnelName ..."
$existing = & cloudflared tunnel list 2>$null | Select-String $TunnelName
if ($existing) {
    Write-Host "隧道已存在，复用。"
} else {
    & cloudflared tunnel create $TunnelName
}

# 2. 取 Tunnel ID
$listOut = & cloudflared tunnel list
$line = $listOut | Where-Object { $_ -match "\s$TunnelName\s" } | Select-Object -First 1
if (-not $line) { throw "无法解析 Tunnel ID，请手动检查 cloudflared tunnel list" }
$tunnelId = ($line -split "\s+")[0]
Write-Host "Tunnel ID: $tunnelId"

# 3. 绑定 DNS
Write-Host "绑定 $Hostname -> $TunnelName ..."
& cloudflared tunnel route dns $TunnelName $Hostname

# 4. 用真实 Tunnel ID 写入配置
$cfg = Join-Path $PSScriptRoot "cloudflared_config.yml"
$text = Get-Content $cfg -Raw
$text = $text -replace "<TUNNEL_ID>", $tunnelId
Set-Content -Path $cfg -Value $text -NoNewline -Encoding UTF8
Write-Host "已更新配置: $cfg"

Write-Host ""
Write-Host "完成。启动隧道:"
Write-Host "  .\start_tunnel.ps1 -Background"
Write-Host "验证:"
Write-Host "  curl https://$Hostname/health"
