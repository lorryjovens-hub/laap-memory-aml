# 安装 cloudflared（Windows）
# 用法: .\install_cloudflared.ps1 [-Version <tag>]
param(
    [string]$Version = "latest"
)
$ErrorActionPreference = "Stop"

if (Get-Command cloudflared -ErrorAction SilentlyContinue) {
    Write-Host "cloudflared 已安装:" (cloudflared --version)
    exit 0
}

$arch = if ([Environment]::Is64BitOperatingSystem) { "amd64" } else { "386" }
if ($Version -eq "latest") {
    $url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-$arch.exe"
} else {
    $url = "https://github.com/cloudflare/cloudflared/releases/download/$Version/cloudflared-windows-$arch.exe"
}

$dest = Join-Path $env:LOCALAPPDATA "cloudflared"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$exe = Join-Path $dest "cloudflared.exe"

Write-Host "下载 cloudflared -> $exe"
Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing

# 加入用户 PATH（幂等）
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$dest*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$dest", "User")
    Write-Host "已把 $dest 加入用户 PATH（新开终端生效）"
}

Write-Host "安装完成:" (& $exe --version)
Write-Host ""
Write-Host "下一步: cloudflared tunnel login"
