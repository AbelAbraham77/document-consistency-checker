# Prefer an installed Node.js; fall back to the verified workspace runtime.
$ErrorActionPreference = 'Stop'
if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) {
    $runtime = Get-ChildItem (Join-Path $PSScriptRoot '../.tools') -Directory -Filter 'node-*-win-x64' -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName 'node.exe') } |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $runtime) { throw 'Install Node.js 20.9+ before starting the frontend.' }
    $env:PATH = $runtime.FullName + ';' + $env:PATH
}
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path 'node_modules/next')) {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
    & npm.cmd run dev
} finally { Pop-Location }
