# Fonctions partagées par lancer.ps1 et service.ps1 (Windows) : Python, port de la page, état du dashboard.

$Venv = if ($env:VENV) { $env:VENV } else { Join-Path $HOME ".venvs\obd" }
$Python = Join-Path $Venv "Scripts\python.exe"
$Pythonw = Join-Path $Venv "Scripts\pythonw.exe"   # sans fenêtre : pour le lancement à l'ouverture de session
$Raccourci = Join-Path ([Environment]::GetFolderPath("Startup")) "Car Sentinel.lnk"
$env:PYTHONUTF8 = "1"          # fichiers et journal en UTF-8, comme sous Linux
$env:PYTHONUNBUFFERED = "1"

function Port-Page {
    # PORT_PAGE de car.conf, 8080 par défaut
    $p = "8080"
    if (Test-Path car.conf) {
        Get-Content car.conf | ForEach-Object {
            if ($_ -match "^\s*PORT_PAGE\s*=\s*(\d+)") { $p = $Matches[1] }
        }
    }
    return $p
}

function En-Route($port) {
    # Le dashboard répond-il (et pas un autre programme sur ce port) ?
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$port/api/state" -UseBasicParsing -TimeoutSec 1
        return $r.Content -match '"status"'
    } catch {
        return $false
    }
}

function Processus-Dashboard {
    # Processus Python qui font tourner src\car_sentinel.py
    @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%.exe'" |
      Where-Object { $_.CommandLine -match "car_sentinel\.py" })
}

function Trouver-Python {
    # Python 3.9 ou plus : lanceur py, sinon python (pas le raccourci du Microsoft Store, qui n'est pas Python)
    $code = "import sys; print(sys.executable if sys.version_info >= (3, 9) else '')"
    $exe = $null
    if (Get-Command py -ErrorAction SilentlyContinue) { $exe = py -3 -c $code 2>$null }
    if (-not $exe -and (Get-Command python -ErrorAction SilentlyContinue)) { $exe = python -c $code 2>$null }
    if ($exe) { return "$exe".Trim() }
    return $null
}

function Preparer-Python {
    # Environnement Python : créé et rempli au premier lancement.
    if (Test-Path $Python) {
        & $Python -c "import obd" 2>$null
        if ($LASTEXITCODE -eq 0) { return }
    }
    $base = Trouver-Python
    if (-not $base) {
        Write-Host "→ Python introuvable : installation (winget)" -ForegroundColor Cyan
        winget install --exact --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
        $base = Trouver-Python
        if (-not $base) { throw "Python toujours introuvable : installer Python 3 (python.org), puis relancer." }
    }
    Write-Host "→ Installation des dépendances dans $Venv" -ForegroundColor Cyan
    & $base -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "création de $Venv impossible" }
    & $Python -m pip install -q --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "installation des dépendances impossible (connexion internet ?)" }
}
