$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

if (!(Test-Path $Python)) {
    python -m venv $Venv
}

& $Python -m pip install -r (Join-Path $Root "requirements.txt")
& $Python (Join-Path $Root "server.py")
