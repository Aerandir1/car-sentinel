# Passe l'adaptateur ELM327 USB au conteneur Docker (Docker Desktop sous Windows).
#
# Docker Desktop ne voit pas les ports COM de Windows : la clé doit être attachée à WSL2 par usbipd.
# Ce script, à lancer une fois depuis le dossier du projet (clé branchée, Docker Desktop démarré) :
#   1. installe usbipd si besoin,
#   2. repère la clé (CH340, FTDI, CP210x, Prolific, port série USB…) et la partage (usbipd bind),
#   3. crée une tâche planifiée qui la rattache à WSL2 à chaque ouverture de session et à chaque branchement,
#   4. vérifie que le conteneur la voit, et corrige OBD_GID dans clio.conf si besoin.
#
#   powershell -ExecutionPolicy Bypass -File .\windows-usb.ps1            # installation
#   powershell -ExecutionPolicy Bypass -File .\windows-usb.ps1 -Supprimer # retire la tâche et le partage

param([switch]$Supprimer)

$ErrorActionPreference = "Continue"   # erreurs des programmes vérifiées par $LASTEXITCODE (Stop casse sur leur stderr)
[Console]::OutputEncoding = [Text.Encoding]::UTF8   # accents des messages de usbipd et du conteneur
$Tache = "Clio Sentinel - adaptateur USB"
Set-Location $PSScriptRoot

# usbipd bind et la tâche planifiée demandent les droits administrateur : on se relance en admin.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    $opts = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Supprimer) { $opts += "-Supprimer" }
    Start-Process powershell -Verb RunAs -ArgumentList $opts
    exit
}

function Etape($texte) { Write-Host "→ $texte" -ForegroundColor Cyan }
function Erreur($texte) { Write-Host "✗ $texte" -ForegroundColor Red; exit 1 }

function Recharger-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

# --- 1. usbipd ---
if (-not (Get-Command usbipd -ErrorAction SilentlyContinue)) {
    Etape "Installation de usbipd (winget)"
    winget install --exact --id dorssel.usbipd-win --accept-source-agreements --accept-package-agreements
    Recharger-Path
    if (-not (Get-Command usbipd -ErrorAction SilentlyContinue)) {
        Erreur "usbipd introuvable après l'installation : fermer cette fenêtre et relancer le script."
    }
}
$usbipd = (Get-Command usbipd).Source
usbipd state *> $null
if ($LASTEXITCODE -ne 0) {   # « state » et « --hardware-id » : usbipd 4 ou plus
    Etape "Mise à jour de usbipd"
    winget upgrade --exact --id dorssel.usbipd-win --accept-source-agreements --accept-package-agreements
}

function Peripheriques { (usbipd state | Out-String | ConvertFrom-Json).Devices }

function VidPid($p) {
    if ($p.InstanceId -match "VID_([0-9A-F]{4})&PID_([0-9A-F]{4})") { return "$($Matches[1]):$($Matches[2])".ToLower() }
    return $null
}

function Retirer-Tache {
    Stop-ScheduledTask -TaskName $Tache -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Tache -Confirm:$false -ErrorAction SilentlyContinue
}

if ($Supprimer) {
    Retirer-Tache
    $conf = Join-Path $PSScriptRoot "windows-usb.id"
    if (Test-Path $conf) {
        $id = (Get-Content $conf -Raw).Trim()
        usbipd detach --hardware-id $id 2>$null
        usbipd unbind --hardware-id $id
        Remove-Item $conf
    }
    Etape "Tâche et partage supprimés"
    exit
}

# --- 2. Repérer la clé ---
# Puces USB-série des ELM327 : CH340/CH341, FTDI, CP210x, Prolific, QBD ; sinon tout « port série / COM »
$vids = "1a86", "0403", "10c4", "067b", "0918"
$liste = @(Peripheriques | Where-Object { $_.BusId -and (VidPid $_) })
$cles = @($liste | Where-Object {
    $vids -contains (VidPid $_).Split(":")[0] -or $_.Description -match "COM\d|s[ée]rie|serial|ELM|OBD"
})
if ($cles.Count -eq 0) {
    Write-Host "Périphériques USB branchés :"
    $liste | ForEach-Object { Write-Host "   $($_.BusId)  $(VidPid $_)  $($_.Description)" }
    Erreur "Aucun adaptateur série USB trouvé : brancher l'ELM327 puis relancer le script."
}
if ($cles.Count -gt 1) {
    Write-Host "Plusieurs ports série USB :"
    for ($i = 0; $i -lt $cles.Count; $i++) { Write-Host "   [$($i + 1)] $($cles[$i].BusId)  $($cles[$i].Description)" }
    $n = [int](Read-Host "Numéro de l'ELM327")
    if ($n -lt 1 -or $n -gt $cles.Count) { Erreur "Choix invalide" }
    $cle = $cles[$n - 1]
} else {
    $cle = $cles[0]
}
$id = VidPid $cle
Etape "Adaptateur : $($cle.Description) (BUSID $($cle.BusId), $id)"
# Repéré par VID:PID et pas par BUSID : il reste reconnu branché sur un autre port USB
Set-Content (Join-Path $PSScriptRoot "windows-usb.id") $id

# --- 3. Partage et rattachement automatique ---
if (-not $cle.PersistedGuid) {
    Etape "Partage de la clé avec WSL2 (usbipd bind)"
    usbipd bind --hardware-id $id
    if ($LASTEXITCODE -ne 0) { Erreur "usbipd bind a échoué" }
}

if (-not (wsl -l -q | Out-String).Replace("`0", "").Contains("docker-desktop")) {
    Erreur "Docker Desktop (moteur WSL2) ne tourne pas : le démarrer puis relancer le script."
}

# Tâche à l'ouverture de session : usbipd rattache la clé à WSL2 tant qu'elle tourne, à chaque branchement.
# Elle redémarre toute seule si elle s'arrête (WSL2 pas encore prêt au démarrage de Windows…).
Etape "Tâche planifiée « $Tache » (rattache la clé à chaque branchement)"
Retirer-Tache
$action = New-ScheduledTaskAction -Execute $usbipd -Argument "attach --wsl --hardware-id $id --auto-attach"
$declencheur = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$declencheur.Delay = "PT30S"
$reglages = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $Tache -Action $action -Trigger $declencheur -Settings $reglages `
    -Principal $principal | Out-Null
Start-ScheduledTask -TaskName $Tache

Etape "Attente du rattachement"
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep 1
    if (Peripheriques | Where-Object { (VidPid $_) -eq $id -and $_.ClientIPAddress }) { $ok = $true; break }
}
if (-not $ok) {
    Erreur "La clé n'est pas attachée à WSL2. Essayer à la main : usbipd attach --wsl --hardware-id $id"
}
Write-Host "✓ Clé attachée à WSL2" -ForegroundColor Green

# --- 4. Le conteneur la voit-il ? ---
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { exit }
if (-not (Test-Path clio.conf)) { Copy-Item clio.conf.exemple clio.conf }
if (-not (docker compose ps -q clio 2>$null)) {
    Etape "Lancement du conteneur"
    docker compose --env-file clio.conf up -d --build
}
Etape "Recherche de l'adaptateur par le conteneur (jusqu'à 40 s)"
$depart = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
docker compose restart clio | Out-Null
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep 1
    $journal = docker compose logs --no-log-prefix --since $depart clio 2>$null | Out-String
    if ($journal -match "Adaptateur trouv[^\r\n]*") {
        Write-Host "✓ $($Matches[0])" -ForegroundColor Green
        exit
    }
    if ($journal -match "OBD_GID=(\d+)") {
        $gid = $Matches[1]
        Etape "Droit d'ouvrir le port : OBD_GID=$gid dans clio.conf, relance du conteneur"
        $conf = Get-Content clio.conf
        if ($conf -match "^\s*OBD_GID\s*=") { $conf = $conf -replace "^\s*OBD_GID\s*=.*", "OBD_GID=$gid" }
        else { $conf += "OBD_GID=$gid" }
        Set-Content clio.conf $conf
        docker compose --env-file clio.conf up -d | Out-Null
        $depart = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
}
Write-Host "Le conteneur ne trouve pas l'adaptateur. Ports vus par le conteneur :"
docker compose exec -T clio sh -c "ls -l /hostdev/ | grep -E 'ttyUSB|ttyACM' || echo '   aucun'"
Write-Host "Journal : docker compose logs --tail 30"
exit 1
