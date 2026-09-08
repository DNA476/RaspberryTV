param([switch]$Setup)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDir
$pythonPath = Join-Path $projectDir '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment' }
    $Setup = $true
}
if ($Setup) {
    & $pythonPath -m pip install 'PySide6-Essentials>=6.8,<7'
    if ($LASTEXITCODE -ne 0) { throw 'Could not install Qt' }
}
& $pythonPath -m raspberry_tv --preview --windowed
