# PowerShell script to start the AI Agent Gateway with proper Unicode support

# Set Python to use UTF-8 encoding
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# Set console output to UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "Starting AI Agent Gateway with UTF-8 encoding..." -ForegroundColor Green
Write-Host "Environment configured for Unicode/Emoji support" -ForegroundColor Cyan

# Ensure script runs from its directory
Set-Location $PSScriptRoot

# Activate virtual environment if present
$VenvActivate = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $VenvActivate) {
    & $VenvActivate
}

# Start uvicorn using the venv Python or uvicorn
$PythonExe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $PythonExe) {
    & $PythonExe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
} else {
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
}
