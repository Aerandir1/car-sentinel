# Ouvre le dashboard Clio dans le navigateur, en le lançant s'il ne tourne pas déjà (Windows ; lancer.sh sous Linux).
#
#   .\lancer.bat                                   # double-clic : service installé (service.ps1) : le relance au besoin ;
#                                                  # sinon lance le script dans cette fenêtre
#   .\lancer.bat --port COM3 --log trajet.csv      # lance le script ici avec ces options (sans le service)
#   powershell -ExecutionPolicy Bypass -File .\lancer.ps1 [options]     # la même chose depuis PowerShell
#
# Réglages dans clio.conf ; les options passées ici vont à src/dashboard_clio.py et passent devant (voir README).
# $env:NO_BROWSER=1 pour ne pas ouvrir le navigateur.

$ErrorActionPreference = "Continue"   # erreurs des programmes vérifiées par $LASTEXITCODE (Stop casse sur leur stderr)
[Console]::OutputEncoding = [Text.Encoding]::UTF8   # accents des messages du script
Set-Location $PSScriptRoot
. (Join-Path $PSScriptRoot "windows-commun.ps1")

# Port de la page : --http s'il est passé, sinon PORT_PAGE de clio.conf
$port = Port-Page
for ($i = 0; $i -lt $args.Count; $i++) {
    if ($args[$i] -eq "--http" -and $i + 1 -lt $args.Count) { $port = $args[$i + 1] }
    elseif ($args[$i] -like "--http=*") { $port = $args[$i].Substring(7) }
}
$page = "http://clio-sentinel.localhost:$port"

function Ouvrir-Page {
    if ($env:NO_BROWSER) { return }
    Write-Host "→ Page : $page"
    Start-Process $page
}

# Déjà en route : on ouvre juste la page.
if ($args.Count -eq 0 -and (En-Route $port)) {
    Ouvrir-Page
    exit 0
}

# Service installé et pas d'options : c'est lui qui fait tourner le script (pas de deuxième copie ici).
if ($args.Count -eq 0 -and (Test-Path $Raccourci)) {
    & (Join-Path $PSScriptRoot "service.ps1") restart
    if (En-Route $port) { Ouvrir-Page }
    exit
}

# Deux copies se disputeraient l'ELM327 et la base
if (Processus-Dashboard) {
    Write-Host "Le dashboard tourne déjà : powershell -ExecutionPolicy Bypass -File .\service.ps1 stop d'abord"
    Write-Host "pour le lancer ici avec des options (ou les mettre dans AUTRES_OPTIONS de clio.conf)"
    exit 1
}

Preparer-Python

Write-Host "→ Lancement du dashboard (Ctrl+C pour arrêter)"
$opts = @("src\dashboard_clio.py") + ($args | ForEach-Object { '"' + $_ + '"' })
$proc = Start-Process $Python -ArgumentList $opts -NoNewWindow -PassThru
# Ouvre le navigateur une fois le serveur prêt.
for ($i = 0; $i -lt 30 -and -not $proc.HasExited; $i++) {
    if (En-Route $port) { Ouvrir-Page; break }
    Start-Sleep -Milliseconds 500
}
$proc.WaitForExit()
exit $proc.ExitCode
