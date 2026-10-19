# Enregistrement permanent sous Windows (service.sh sous Linux) : le dashboard tourne en fond, sans fenêtre, et
# démarre à chaque ouverture de session (raccourci dans le dossier Démarrage, sans droits administrateur).
# Il attend l'ELM327, enregistre dès que le calculateur répond, et reprend tout seul après une coupure.
#
#   powershell -ExecutionPolicy Bypass -File .\service.ps1 install    # installe et démarre (une seule fois)
#   powershell -ExecutionPolicy Bypass -File .\service.ps1 restart    # relance, après un changement dans car.conf
#   powershell -ExecutionPolicy Bypass -File .\service.ps1 status     # tourne-t-il ? adresses de la page, ou l'erreur
#   powershell -ExecutionPolicy Bypass -File .\service.ps1 logs       # journal en direct (Ctrl+C pour quitter)
#   powershell -ExecutionPolicy Bypass -File .\service.ps1 stop       # arrête jusqu'à la prochaine session (start pour relancer)
#   powershell -ExecutionPolicy Bypass -File .\service.ps1 remove     # arrête et supprime le lancement automatique
#
# Les réglages (accès réseau, serveur, adaptateur…) sont dans car.conf, créé au premier lancement.
# Le journal est dans logs\car.log.

param([string]$Action = "")

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location $PSScriptRoot
. (Join-Path $PSScriptRoot "windows-commun.ps1")
$Journal = Join-Path $PSScriptRoot "logs\car.log"

function Arreter {
    Processus-Dashboard | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Demarrer {
    Arreter
    Start-Process $Pythonw -ArgumentList "src\car_sentinel.py" -WorkingDirectory $PSScriptRoot
}

# Lignes du journal depuis le dernier démarrage
function Dernier-Journal {
    if (-not (Test-Path $Journal)) { return @() }
    $lignes = @(Get-Content $Journal -Tail 300 -Encoding UTF8)
    $debut = 0
    for ($i = 0; $i -lt $lignes.Count; $i++) { if ($lignes[$i] -like "--- *") { $debut = $i } }
    return @($lignes[$debut..($lignes.Count - 1)] | Where-Object { $_ -notmatch "^\[obd\." })
}

# La page répond-elle ? Oui : adresses et avertissements. Non : l'erreur.
function Verifier {
    $port = Port-Page
    for ($i = 0; $i -lt 20; $i++) {
        if (En-Route $port) {
            Write-Host "✓ Dashboard en route" -ForegroundColor Green
            Dernier-Journal | Where-Object { $_ -match "^(Dashboard|  sur le réseau|  \(pas de mot|Synchro|Attention|Réglages|Adaptateur)" } |
                Select-Object -First 15 | ForEach-Object { Write-Host $_ }
            return $true
        }
        if (-not (Processus-Dashboard)) { break }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "✗ Le dashboard ne démarre pas (service.ps1 logs pour le journal complet) :" -ForegroundColor Red
    Dernier-Journal | Select-Object -Last 8 | ForEach-Object { Write-Host "    $_" }
    return $false
}

function Installe {
    if (-not (Test-Path $Raccourci)) {
        Write-Host "Lancement automatique pas installé : powershell -ExecutionPolicy Bypass -File .\service.ps1 install"
        exit 1
    }
}

switch ($Action) {
    "install" {
        Preparer-Python
        if (-not (Test-Path car.conf)) {
            Copy-Item car.conf.exemple car.conf
            Write-Host "→ Réglages créés dans $PSScriptRoot\car.conf"
        }
        $sh = New-Object -ComObject WScript.Shell
        $lnk = $sh.CreateShortcut($Raccourci)
        $lnk.TargetPath = $Pythonw
        $lnk.Arguments = "src\car_sentinel.py"
        $lnk.WorkingDirectory = $PSScriptRoot
        $lnk.Description = "Car Sentinel : enregistrement OBD permanent"
        $lnk.Save()
        Demarrer
        Write-Host "→ Installé : démarre à chaque ouverture de session (réglages : $PSScriptRoot\car.conf)"
        Verifier | Out-Null
    }
    { $_ -in "start", "restart" } {
        Installe
        Demarrer
        if (-not (Verifier)) { exit 1 }
    }
    "status" {
        Installe
        if (-not (Verifier)) { exit 1 }
    }
    "logs" {
        Get-Content $Journal -Tail 30 -Wait -Encoding UTF8
    }
    "stop" {
        Installe
        Arreter
        Write-Host "→ Arrêté (service.ps1 start pour relancer ; il redémarrera aussi à la prochaine session)"
    }
    "remove" {
        Arreter
        Remove-Item $Raccourci -ErrorAction SilentlyContinue
        Write-Host "→ Lancement automatique supprimé"
    }
    default {
        Get-Content $PSCommandPath -Encoding UTF8 | Select-Object -Skip 1 -First 12 | ForEach-Object { $_ -replace "^# ?", "" }
        exit 1
    }
}
