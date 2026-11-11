$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path dify | Out-Null
Invoke-WebRequest -Uri "https://gh-proxy.com/https://github.com/langgenius/dify/archive/refs/tags/1.19.0.tar.gz" -OutFile "dify-1.19.0.tar.gz"
tar -xzf "dify-1.19.0.tar.gz" -C dify --strip-components=2 "dify-1.19.0/docker"
Write-Host "Dify 1.19.0 docker files extracted to: $PSScriptRoot\dify"
