// Appli Windows de Car Sentinel : fait tourner le dashboard (src/car_sentinel.py, avec le Python embarqué) en fond,
// l'affiche dans sa fenêtre, et montre dans la zone de notification si l'ELM327, la voiture et le serveur répondent.
// Réglages, bases et journal dans %APPDATA%\Car Sentinel (voir python/desktop_main.py).

const { app, BrowserWindow, Menu, Notification, Tray, nativeImage, shell } = require("electron");
const { autoUpdater } = require("electron-updater");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const path = require("path");

const ID = "fr.car.sentinel";   // = appId de package.json (notifications Windows)
const CACHE = "--cache";        // lancement à l'ouverture de session : icône seule, sans fenêtre
const POLL_MS = 2000;
const RELANCE_MS = 5000;        // le service s'est arrêté : relancé au plus tôt ce délai après le lancement précédent
const ARRIVEE_MS = 3000;        // voiture reconnue depuis ce temps : bulle « branchée » (VIN lu entre-temps)
const DEPART_MS = 20000;        // calculateur muet depuis ce temps : bulle « débranchée » (pas au démarrage du moteur)
const MAJ_MS = 6 * 3600 * 1000; // mise à jour cherchée sur le serveur dès qu'il répond, puis toutes les 6 h

const USER = app.getPath("userData");
const CONF = path.join(USER, "car.conf");
const JOURNAL = path.join(USER, "logs", "car.log");
const RES = app.isPackaged ? process.resourcesPath : __dirname;   // Python, version (hors de app.asar)
const ICONES = path.join(__dirname, "icones");

let tray = null, win = null, service = null, quitter = false, lance = 0, premiere = true;
let etat = null;          // dernière réponse de /api/state, null si le service ne répond pas
let externe = false;      // un dashboard tournait déjà sur le port (service.ps1…) : on s'en sert sans le lancer
let titres = {};          // id de voiture → titre (/api/meta)
let icone = "", infobulle = "", menuTexte = "";
let annonce = { voiture: null, depuis: null, ok: false };   // voiture annoncée par bulle
let maj = { etat: "", version: null, cherchee: 0, manuelle: false };   // mise à jour (voir « Mise à jour »)

// ——— Réglages ———

function port() {
  try {
    const m = fs.readFileSync(CONF, "utf8").match(/^\s*PORT_PAGE\s*=\s*["']?(\d+)/m);
    if (m) return Number(m[1]);
  } catch {}
  return 8080;
}
const page = () => `http://car-sentinel.localhost:${port()}`;

function version() {
  try { return fs.readFileSync(path.join(RES, "version.txt"), "utf8").trim(); } catch { return "dev"; }
}

// ——— Le service (Python) ———

function api(chemin) {
  return new Promise(ok => {
    const req = http.get({ host: "127.0.0.1", port: port(), path: chemin, timeout: 1500 }, r => {
      let corps = "";
      r.setEncoding("utf8");
      r.on("data", d => corps += d);
      r.on("end", () => { try { ok(JSON.parse(corps)); } catch { ok(null); } });
    });
    req.on("timeout", () => req.destroy());
    req.on("error", () => ok(null));
  });
}

function demarrer() {
  if (quitter || service || Date.now() - lance < RELANCE_MS) return;
  lance = Date.now();
  // Appli installée : Python embarqué, sans console ; sinon (npm start) celui du PC (CAR_PYTHON pour en choisir un)
  const python = app.isPackaged ? path.join(RES, "python", "pythonw.exe") : (process.env.CAR_PYTHON || "python3");
  const script = path.join(RES, "python", "desktop_main.py");
  service = spawn(python, [script, USER, version()], {
    cwd: USER, windowsHide: true, stdio: app.isPackaged ? "ignore" : "inherit",
    env: { ...process.env, PYTHONUTF8: "1", PYTHONUNBUFFERED: "1" },
  });
  service.on("error", e => console.error(`Service : ${e.message}`));
  service.on("exit", () => { service = null; });
}

function arreter() {
  if (service) service.kill();
  service = null;
}

async function verifier() {
  const s = await api("/api/state");
  if (premiere) {
    premiere = false;
    externe = !!s;   // au lancement, un autre dashboard occupe déjà le port : on s'en sert
  }
  if (!s && !service) {
    externe = false;
    demarrer();
  }
  etat = s;
  majAuto();
  if (s && s.car && !titres[s.car]) {
    const m = await api("/api/meta");
    for (const c of (m && m.cars) || []) titres[c.id] = titres[c.id] || c.name;
    if (m && m.car) titres[m.car.id] = m.car.title;
  }
  afficher();
}

// ——— Mise à jour ———
// Le serveur de la maison (clio-sentinel-server, /maj/) garde le dernier installeur, publié par
// npm run publier (scripts/publier.py). On le cherche à l'adresse qui répond à la synchro (maison ou VPN) :
// téléchargé en fond, installé à la fermeture de l'appli, ou tout de suite depuis la bulle ou le menu.

autoUpdater.autoDownload = true;
autoUpdater.autoInstallOnAppQuit = true;
autoUpdater.disableDifferentialDownload = true;   // le serveur n'envoie pas de morceaux (Range) : fichier entier
autoUpdater.logger = null;

autoUpdater.on("checking-for-update", () => majEtat("recherche"));
autoUpdater.on("update-not-available", () => {
  majEtat("aucune");
  if (maj.manuelle) notifier("Car Sentinel est à jour", `Version ${app.getVersion()}`);
  maj.manuelle = false;
});
autoUpdater.on("update-available", i => { maj.version = i.version; majEtat("telechargement"); });
autoUpdater.on("update-downloaded", i => {
  maj.version = i.version;
  maj.manuelle = false;
  majEtat("prete");
  notifier(`Mise à jour ${i.version} prête`, "Cliquer pour l'installer maintenant (sinon : à la fermeture de l'appli).",
           installerMaj);
});
autoUpdater.on("error", e => {
  console.error(`Mise à jour : ${e.message}`);
  if (maj.manuelle) notifier("Mise à jour impossible", e.message.split("\n")[0].slice(0, 200));
  maj.manuelle = false;
  majEtat(maj.etat === "prete" ? "prete" : "erreur");
});

function majEtat(e) {
  maj.etat = e;
  afficher();
}

// Adresse du serveur qui répond (celle de la synchro), null s'il ne répond pas ou n'est pas réglé
const adresseServeur = () => (etat && etat.sync && etat.sync.ok === true && etat.sync.url) || null;

function chercherMaj(manuelle = false) {
  if (!app.isPackaged || ["recherche", "telechargement", "prete"].includes(maj.etat)) {
    if (manuelle && !app.isPackaged) notifier("Mise à jour", "Seulement dans l'appli installée (pas avec npm start).");
    return;
  }
  const url = adresseServeur();
  if (!url) {
    if (manuelle) notifier("Mise à jour impossible", "Le serveur ne répond pas (Wi-Fi de la maison ou VPN ?).");
    return;
  }
  maj.cherchee = Date.now();
  maj.manuelle = manuelle;
  autoUpdater.setFeedURL({ provider: "generic", url: `${url}/maj/` });
  autoUpdater.checkForUpdates().catch(() => {});   // erreur déjà passée par l'événement « error »
}

// Automatique : dès que le serveur répond, puis toutes les MAJ_MS
function majAuto() {
  if (adresseServeur() && Date.now() - maj.cherchee >= MAJ_MS) chercherMaj();
}

function installerMaj() {
  quitter = true;
  arreter();   // le Python embarqué libéré avant que l'installeur ne le remplace
  autoUpdater.quitAndInstall(true, true);   // sans fenêtre, puis relance l'appli
}

function ligneMaj() {
  switch (maj.etat) {
    case "recherche": return { label: "Recherche d'une mise à jour…", enabled: false };
    case "telechargement": return { label: `Téléchargement de la version ${maj.version}…`, enabled: false };
    case "prete": return { label: `Installer la version ${maj.version} et redémarrer`, click: installerMaj };
    default: return { label: "Rechercher une mise à jour", click: () => chercherMaj(true) };
  }
}

// ——— Ce qu'on montre ———

// Liaison avec la voiture (arc du compte-tours) et serveur (pastille) : desktop/scripts/icones.py
function liaison(s) {
  if (!s) return "arret";
  const { elm, ecu } = s.link || {};
  if (elm === "demo" || ecu === "demo") return "demo";
  if (ecu === "ok") return "ok";
  if (["ok", "occupe", "reconnexion", "ko"].includes(elm)) return "elm";
  return "absent";
}

function serveur(s) {
  const sync = s && s.sync;
  if (!sync) return "aucun";
  return sync.ok === true ? "ok" : sync.ok === false ? "ko" : "attente";
}

const ELM = {
  recherche: "recherche…", absent: "non branché", refuse: "port interdit (droits)",
  occupe: "occupé par le diagnostic", ko: "ne répond pas bien", demo: "mode démo", reconnexion: "reconnexion…",
};
const ECU = { off: "—", recherche: "recherche du protocole…", muet: "muet (contact coupé ?)", sans_tension: "pas de tension (clé hors de la voiture)", demo: "mode démo" };

function heure(t) {
  return new Date(t * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

function lignes(s) {
  if (!s) return ["Service arrêté (redémarrage…)"];
  const l = s.link || {};
  const elm = l.elm === "ok" ? `${l.name || "OK"}${l.port ? ` sur ${l.port}` : ""}` : (ELM[l.elm] || l.elm);
  const ecu = l.ecu === "ok" ? `OK${l.protocol ? ` (${l.protocol.replace(/^ISO 15765-4 /, "")})` : ""}`
                             : (ECU[l.ecu] || l.ecu);
  const sync = s.sync;
  const srv = !sync ? "pas réglé"
            : sync.ok === true ? `connecté${sync.synced ? `, synchro ${heure(sync.synced)}` : ""}`
            : sync.ok === null ? "connexion…" : "injoignable";
  return [`ELM327 : ${elm}`, `Calculateur : ${ecu}`, `Serveur : ${srv}`];
}

function afficher() {
  const nom = `${liaison(etat)}-${serveur(etat)}`;
  if (nom !== icone) {
    icone = nom;
    tray.setImage(image(nom));
  }
  const titre = etat ? `Car Sentinel — ${titres[etat.car] || etat.car}` : "Car Sentinel";
  const texte = [titre, ...lignes(etat)].join("\n");
  if (texte !== infobulle) {
    infobulle = texte;
    tray.setToolTip(texte.length > 127 ? texte.slice(0, 126) + "…" : texte);   // limite de Windows
  }
  const menu = texte + externe + auDemarrage() + maj.etat + maj.version;
  if (menu !== menuTexte) {
    menuTexte = menu;
    tray.setContextMenu(construireMenu(titre, lignes(etat)));
  }
  chargerPage();
  bulles();
}

// Bulle quand la voiture est reconnue, et quand elle s'en va (pas pour le serveur ni la démo)
function bulles() {
  const ok = liaison(etat) === "ok";
  const maintenant = Date.now();
  if (ok !== annonce.ok) {
    if (annonce.depuis === null) annonce.depuis = maintenant;
    const attente = ok ? ARRIVEE_MS : DEPART_MS;
    if (maintenant - annonce.depuis >= attente) {
      annonce.ok = ok;
      annonce.depuis = null;
      if (ok) annonce.voiture = titres[etat.car] || etat.car;
      notifier(ok ? `${annonce.voiture} branchée` : `${annonce.voiture || "Voiture"} débranchée`,
               ok ? "Le calculateur répond : enregistrement en cours." : "Le calculateur ne répond plus.");
    }
  } else {
    annonce.depuis = null;
  }
}

function notifier(titre, corps, clic = montrer) {
  if (!Notification.isSupported()) return;
  const n = new Notification({ title: titre, body: corps, icon: image("appli"), silent: true });
  n.on("click", clic);
  n.show();
}

// ——— Fenêtre, icône, menu ———

// Icônes de desktop/icones (scripts/icones.py) : .ico sous Windows (toutes les tailles d'écran), .png ailleurs
function image(nom) {
  const ico = path.join(ICONES, `${nom}.ico`);
  return nativeImage.createFromPath(process.platform === "win32" && nom !== "appli" ? ico
                                    : path.join(ICONES, `${nom}.png`));
}

// Lancement à l'ouverture de session (Windows : clé Run du registre, retrouvée avec les mêmes arguments)
const auDemarrage = () => app.getLoginItemSettings({ args: [CACHE] }).openAtLogin;
const lancerAuDemarrage = oui => app.setLoginItemSettings({ openAtLogin: oui, args: [CACHE] });

function montrer() {
  if (!win) {
    win = new BrowserWindow({
      width: 1280, height: 820, minWidth: 480, minHeight: 360, title: "Car Sentinel",
      icon: image("appli"), backgroundColor: "#030817", autoHideMenuBar: true, show: false,
    });
    win.removeMenu();
    win.on("ready-to-show", () => win.show());
    win.on("page-title-updated", e => e.preventDefault());
    // Fermer la fenêtre la cache seulement : le service continue, l'icône reste
    win.on("close", e => { if (!quitter) { e.preventDefault(); win.hide(); } });
    // Liens vers ailleurs (clio-hub, serveur…) : dans le navigateur
    win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
    win.loadFile(path.join(__dirname, "attente.html"));   // tant que le service démarre
    win.webContents.once("did-finish-load", chargerPage);
    return;
  }
  chargerPage();
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

// Le dashboard dans la fenêtre, dès que le service répond (au lieu de la page d'attente ou d'erreur)
function chargerPage() {
  const w = win && win.webContents;
  if (etat && w && !w.isLoading() && !w.getURL().startsWith("http")) w.loadURL(page());
}

function construireMenu(titre, etats) {
  return Menu.buildFromTemplate([
    { label: "Ouvrir le dashboard", click: montrer },
    { label: "Ouvrir dans le navigateur", click: () => shell.openExternal(page()) },
    { type: "separator" },
    { label: titre, enabled: false },
    ...etats.map(l => ({ label: l, enabled: false })),
    ...(externe ? [{ label: "(dashboard lancé hors de l'appli)", enabled: false }] : []),
    { type: "separator" },
    { label: "Réglages (car.conf)", click: () => shell.openPath(CONF) },
    { label: "Journal", click: () => shell.openPath(JOURNAL) },
    { label: "Dossier des données", click: () => shell.openPath(USER) },
    { label: "Redémarrer le service", enabled: !externe, click: () => { arreter(); etat = null; afficher(); } },
    {
      label: "Lancer à l'ouverture de session", type: "checkbox", checked: auDemarrage(),
      click: m => { lancerAuDemarrage(m.checked); menuTexte = ""; afficher(); },
    },
    { type: "separator" },
    { label: `Version ${app.getVersion()}`, enabled: false },
    ligneMaj(),
    { type: "separator" },
    { label: "Quitter Car Sentinel", click: () => { quitter = true; app.quit(); } },
  ]);
}

// ——— Lancement ———

if (!app.requestSingleInstanceLock()) {
  app.quit();   // déjà lancée : elle ouvre sa fenêtre (second-instance)
} else {
  app.setAppUserModelId(ID);
  app.on("second-instance", montrer);
  app.on("window-all-closed", () => {});   // l'appli reste dans la zone de notification
  app.on("before-quit", () => { quitter = true; arreter(); });

  app.whenReady().then(() => {
    // Premier lancement : lancée à chaque ouverture de session, comme service.ps1 install (décochable dans le menu)
    const premier = path.join(USER, "appli.json");
    if (!fs.existsSync(premier)) {
      fs.mkdirSync(USER, { recursive: true });
      if (app.isPackaged) lancerAuDemarrage(true);
      fs.writeFileSync(premier, JSON.stringify({ premier_lancement: new Date().toISOString() }));
    }
    tray = new Tray(image("arret-aucun"));
    icone = "arret-aucun";
    tray.on("click", montrer);
    afficher();
    verifier();
    setInterval(verifier, POLL_MS);
    if (!process.argv.includes(CACHE)) montrer();
  });
}
