// Navigation de l'autoradio : carte (MapLibre + OpenStreetMap, ou Google Maps avec une clé API),
// itinéraire OSRM, guidage pas à pas en français, position GPS lue par le script Python (/api/gps)
// ou simulation le long de l'itinéraire quand il n'y a pas de GPS.

const OSRM = "https://routing.openstreetmap.de/routed-car/route/v1/driving/";
const NOMINATIM = "https://nominatim.openstreetmap.org/search";
const STYLES = { night: "https://tiles.openfreemap.org/styles/fiord", day: "https://tiles.openfreemap.org/styles/liberty" };
const FRANCE = [2.4, 46.6];

// ---------- Géométrie (coordonnées [lon, lat], distances en mètres) ----------
const RAD = Math.PI / 180;
function dist([lon1, lat1], [lon2, lat2]) {
  const a = Math.sin((lat2 - lat1) * RAD / 2) ** 2 + Math.cos(lat1 * RAD) * Math.cos(lat2 * RAD) * Math.sin((lon2 - lon1) * RAD / 2) ** 2;
  return 2 * 6371008.8 * Math.asin(Math.sqrt(a));
}
function bearing([lon1, lat1], [lon2, lat2]) {
  const y = Math.sin((lon2 - lon1) * RAD) * Math.cos(lat2 * RAD);
  const x = Math.cos(lat1 * RAD) * Math.sin(lat2 * RAD) - Math.sin(lat1 * RAD) * Math.cos(lat2 * RAD) * Math.cos((lon2 - lon1) * RAD);
  return (Math.atan2(y, x) / RAD + 360) % 360;
}
// Point le plus proche de p sur le segment [a, b], dans un repère local en mètres
function project(p, a, b) {
  const kx = Math.cos(p[1] * RAD) * 111320, ky = 110540;
  const ax = (a[0] - p[0]) * kx, ay = (a[1] - p[1]) * ky, dx = (b[0] - a[0]) * kx, dy = (b[1] - a[1]) * ky;
  const len = dx * dx + dy * dy;
  const t = len ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len)) : 0;
  return { t, d: Math.hypot(ax + dx * t, ay + dy * t) };
}
const lerp = (a, b, t) => a + (b - a) * t;
const lerpAngle = (a, b, t) => a + ((((b - a) % 360) + 540) % 360 - 180) * t;

// ---------- Itinéraire ----------
function buildRoute(json, dest) {
  const r = json.routes[0], coords = r.geometry.coordinates, cum = [0];
  for (let i = 1; i < coords.length; i++) cum.push(cum[i - 1] + dist(coords[i - 1], coords[i]));
  let idx = 0;
  const steps = r.legs.flatMap((l) => l.steps).map((s) => {
    // Position de la manœuvre le long du tracé (le point existe tel quel dans la géométrie)
    let best = idx, bd = Infinity;
    for (let i = idx; i < coords.length; i++) {
      const d = dist(coords[i], s.maneuver.location);
      if (d < bd) { bd = d; best = i; }
      if (d < 0.5 || i - idx > 4000) break;
    }
    idx = best;
    return { ...s, at: cum[best] };
  });
  return { coords, cum, total: cum.at(-1), duration: r.duration, steps, dest };
}
function pointAt(route, along) {
  const { coords, cum } = route;
  let lo = 0, hi = cum.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (cum[mid] <= along) lo = mid; else hi = mid; }
  const seg = cum[hi] - cum[lo] || 1, t = Math.max(0, Math.min(1, (along - cum[lo]) / seg));
  const a = coords[lo], b = coords[hi];
  return { pos: [lerp(a[0], b[0], t), lerp(a[1], b[1], t)], heading: bearing(a, b), idx: lo };
}
// Où en est-on sur le tracé ? On cherche autour de la dernière position connue.
function locate(route, p, hint) {
  const { coords, cum } = route;
  let best = { d: Infinity, i: hint, t: 0 };
  for (let i = Math.max(0, hint - 30); i < Math.min(coords.length - 1, hint + 800); i++) {
    const r = project(p, coords[i], coords[i + 1]);
    if (r.d < best.d) best = { d: r.d, i, t: r.t };
  }
  return { idx: best.i, off: best.d, along: cum[best.i] + best.t * (cum[best.i + 1] - cum[best.i]) };
}

// ---------- Consignes en français ----------
const SIDE = { left: "à gauche", right: "à droite", "slight left": "légèrement à gauche", "slight right": "légèrement à droite",
               "sharp left": "franchement à gauche", "sharp right": "franchement à droite", straight: "tout droit", uturn: "demi-tour" };
const ord = (n) => (n === 1 ? "1re" : `${n}e`);
function instruction(s) {
  const m = s.maneuver, mod = m.modifier, side = SIDE[mod] ?? "";
  switch (m.type) {
    case "depart": return "Partez";
    case "arrive": return "Arrivée à destination";
    case "roundabout": case "rotary": case "roundabout turn":
      return m.exit ? `Au rond-point, prenez la ${ord(m.exit)} sortie` : "Au rond-point, continuez";
    case "exit roundabout": case "exit rotary": return "Sortez du rond-point";
    case "merge": return `Insérez-vous ${side}`.trim();
    case "on ramp": return `Prenez la bretelle ${side}`.trim();
    case "off ramp": return `Prenez la sortie ${side}`.trim();
    case "fork": return `À l'embranchement, restez ${side}`;
    case "end of road": return `Au bout de la route, tournez ${side}`;
    case "new name": case "continue": case "notification": case "use lane":
      return mod === "uturn" ? "Faites demi-tour" : mod?.includes("slight") ? `Serrez ${side.replace("légèrement ", "")}` : "Continuez";
    default:
      if (mod === "uturn") return "Faites demi-tour";
      if (mod === "straight") return "Continuez tout droit";
      return `Tournez ${side}`;
  }
}
const road = (s) => [s.ref, s.name].filter(Boolean).join(" · ") || s.destinations || s.exits && `sortie ${s.exits}` || "";

// Pictogramme de manœuvre : flèche orientée (ou rond-point, demi-tour, drapeau d'arrivée)
const ANGLE = { straight: 0, "slight right": 45, right: 90, "sharp right": 135, "slight left": -45, left: -90, "sharp left": -135 };
function icon(s) {
  const m = s.maneuver, svg = (d) => `<svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  if (m.type === "arrive") return svg('<path d="M14 42V8"/><path d="M14 9h22l-5 7 5 7H14" fill="currentColor" stroke-width="3"/>');
  if (m.type.includes("roundabout") || m.type.includes("rotary"))
    return svg('<circle cx="24" cy="21" r="8"/><path d="M24 44V29"/><path d="M30 15l8-8"/><path d="M30 7h8v8"/>');
  if (m.modifier === "uturn") return svg('<path d="M30 42V18a8 8 0 0 0-16 0v4"/><path d="M8 18l6 6 6-6"/>');
  const a = (ANGLE[m.modifier] ?? 0) * RAD, x = 24 + Math.sin(a) * 16, y = 22 - Math.cos(a) * 16;
  const h1 = a - 2.5, h2 = a + 2.5, hx = (k) => x - Math.sin(k) * 8, hy = (k) => y + Math.cos(k) * 8;
  return svg(`<path d="M24 44V22L${x.toFixed(1)} ${y.toFixed(1)}"/><path d="M${hx(h1).toFixed(1)} ${hy(h1).toFixed(1)}L${x.toFixed(1)} ${y.toFixed(1)}L${hx(h2).toFixed(1)} ${hy(h2).toFixed(1)}"/>`);
}

const fmtDist = (m) => (m >= 10000 ? `${Math.round(m / 1000)} km` : m >= 950 ? `${(m / 1000).toFixed(1).replace(".", ",")} km`
                       : m >= 100 ? `${Math.round(m / 50) * 50} m` : `${Math.max(0, Math.round(m / 10) * 10)} m`);
const fmtDur = (s) => { const m = Math.round(s / 60); return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`; };
const fmtTime = (d) => d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });

// ---------- Moteurs de carte ----------
const CAR_SVG = '<svg viewBox="0 0 40 40" width="58" height="58"><path d="M20 3l13 32-13-7-13 7z" fill="#f0b429" stroke="#17130a" stroke-width="2.4" stroke-linejoin="round"/></svg>';
const DEST_SVG = '<svg viewBox="0 0 32 42" width="32" height="42"><path d="M16 41s14-15 14-25a14 14 0 0 0-28 0c0 10 14 25 14 25z" fill="#f85149" stroke="#fff" stroke-width="2"/><circle cx="16" cy="16" r="5" fill="#fff"/></svg>';
function el(html, cls) { const d = document.createElement("div"); d.className = cls; d.innerHTML = html; return d; }

async function osmEngine(container, night) {
  if (!document.getElementById("maplibre-css")) {
    document.head.append(Object.assign(document.createElement("link"), { id: "maplibre-css", rel: "stylesheet", href: "/vendor/maplibre/maplibre-gl.css" }));
  }
  const ml = await import("/vendor/maplibre/maplibre-gl.mjs");
  const map = new ml.Map({ container, style: night ? STYLES.night : STYLES.day, center: FRANCE, zoom: 5, maxPitch: 70,
                           attributionControl: { compact: true }, fadeDuration: 0 });
  let route = null, traveled = null;
  const car = new ml.Marker({ element: el(CAR_SVG, "nav-car"), rotationAlignment: "map", pitchAlignment: "map" });
  const dest = new ml.Marker({ element: el(DEST_SVG, "nav-pin"), anchor: "bottom" });
  const line = (coords) => ({ type: "Feature", geometry: { type: "LineString", coordinates: coords?.length > 1 ? coords : [] } });
  // Les couches de l'itinéraire sont remises après chaque changement de style (jour/nuit)
  const addLayers = () => {
    if (map.getSource("route")) return;
    map.addSource("route", { type: "geojson", data: line(route) });
    map.addSource("done", { type: "geojson", data: line(traveled) });
    const before = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
    map.addLayer({ id: "done", type: "line", source: "done", layout: { "line-cap": "round", "line-join": "round" },
                   paint: { "line-color": "#6b7684", "line-width": ["interpolate", ["linear"], ["zoom"], 8, 4, 16, 11], "line-opacity": .7 } }, before);
    map.addLayer({ id: "route-casing", type: "line", source: "route", layout: { "line-cap": "round", "line-join": "round" },
                   paint: { "line-color": "#17130a", "line-width": ["interpolate", ["linear"], ["zoom"], 8, 7, 16, 17] } }, before);
    map.addLayer({ id: "route", type: "line", source: "route", layout: { "line-cap": "round", "line-join": "round" },
                   paint: { "line-color": "#f0b429", "line-width": ["interpolate", ["linear"], ["zoom"], 8, 4, 16, 11] } }, before);
  };
  map.on("style.load", addLayers);
  // Icônes absentes du style : remplacées par une image vide (évite les avertissements en boucle)
  map.on("styleimagemissing", (e) => { if (!map.hasImage(e.id)) map.addImage(e.id, { width: 1, height: 1, data: new Uint8Array(4) }); });
  await new Promise((resolve, reject) => { map.once("load", resolve); map.once("error", (e) => reject(e.error)); });
  return {
    kind: "osm",
    setNight(n) { map.setStyle(n ? STYLES.night : STYLES.day); },
    setRoute(coords, done) {
      route = coords; traveled = done;
      map.getSource("route")?.setData(line(route));
      map.getSource("done")?.setData(line(traveled));
    },
    setCar(p, heading) { if (!p) return car.remove(); car.setLngLat(p).setRotation(heading ?? 0); if (!car._map) car.addTo(map); },
    setDest(p) { if (!p) return dest.remove(); dest.setLngLat(p); if (!dest._map) dest.addTo(map); },
    view({ center, bearing: b, pitch, zoom, offset }) {
      map.jumpTo({ center, bearing: b, pitch, zoom, padding: { top: offset, bottom: 0, left: 0, right: 0 } });
    },
    fit(coords, pad) {
      const b = coords.reduce((acc, c) => acc.extend(c), new ml.LngLatBounds(coords[0], coords[0]));
      map.fitBounds(b, { padding: pad, bearing: 0, pitch: 0, duration: 800 });
    },
    zoomBy(d) { map.easeTo({ zoom: map.getZoom() + d, duration: 250 }); },
    onUser(cb) { for (const t of ["dragstart", "wheel", "rotatestart", "pitchstart"]) map.on(t, (e) => e.originalEvent && cb()); },
    resize() { map.resize(); },
    destroy() { map.remove(); },
  };
}

let googleLoading = null;
async function googleEngine(container, night, key, mapId) {
  googleLoading ??= new Promise((resolve, reject) => {
    window.__clioGoogleReady = resolve;
    window.gm_authFailure = () => reject(new Error("clé Google Maps refusée"));
    const s = document.createElement("script");
    s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&loading=async&libraries=marker&callback=__clioGoogleReady`;
    s.onerror = () => reject(new Error("Google Maps injoignable"));
    document.head.append(s);
  });
  await googleLoading;
  const g = google.maps;
  const map = new g.Map(container, {
    center: { lat: FRANCE[1], lng: FRANCE[0] }, zoom: 6, mapId: mapId || "DEMO_MAP_ID", renderingType: g.RenderingType.VECTOR,
    colorScheme: night ? g.ColorScheme.DARK : g.ColorScheme.LIGHT, disableDefaultUI: true, gestureHandling: "greedy",
    headingInteractionEnabled: true, tiltInteractionEnabled: true,
  });
  new g.TrafficLayer().setMap(map);
  const ll = (p) => ({ lat: p[1], lng: p[0] });
  const casing = new g.Polyline({ map, strokeColor: "#17130a", strokeWeight: 12, zIndex: 1 });
  const lineRoute = new g.Polyline({ map, strokeColor: "#f0b429", strokeWeight: 7, zIndex: 2 });
  const lineDone = new g.Polyline({ map, strokeColor: "#6b7684", strokeOpacity: .7, strokeWeight: 7, zIndex: 1 });
  const carEl = el(CAR_SVG, "nav-car");
  const car = new g.marker.AdvancedMarkerElement({ content: carEl, zIndex: 10 });
  const dest = new g.marker.AdvancedMarkerElement({ content: el(DEST_SVG, "nav-pin"), zIndex: 9 });
  let carHeading = 0;
  const turnCar = () => (carEl.style.transform = `rotate(${carHeading - (map.getHeading() ?? 0)}deg)`);
  map.addListener("heading_changed", turnCar);
  return {
    kind: "google",
    setNight() { /* le thème Google se choisit à la création : le moteur est recréé */ },
    setRoute(coords, done) {
      lineRoute.setPath((coords ?? []).map(ll)); casing.setPath((coords ?? []).map(ll)); lineDone.setPath((done ?? []).map(ll));
    },
    setCar(p, heading) { car.map = p ? map : null; if (p) { car.position = ll(p); carHeading = heading ?? 0; turnCar(); } },
    setDest(p) { dest.map = p ? map : null; if (p) dest.position = ll(p); },
    // Le zoom MapLibre (tuiles de 512 px) vaut celui de Google (256 px) moins 1
    view({ center, bearing: b, pitch, zoom, offset }) {
      // Décale le centre vers l'avant pour placer la voiture dans le bas de l'écran
      const ahead = offset ? (offset / 256) * 156543 * Math.cos(center[1] * RAD) / 2 ** (zoom + 1) * Math.cos(pitch * RAD) : 0;
      const c = [center[0] + Math.sin(b * RAD) * ahead / (111320 * Math.cos(center[1] * RAD)), center[1] + Math.cos(b * RAD) * ahead / 110540];
      map.moveCamera({ center: ll(c), heading: b, tilt: pitch, zoom: zoom + 1 });
    },
    fit(coords, pad) {
      const bounds = new g.LatLngBounds();
      coords.forEach((c) => bounds.extend(ll(c)));
      map.moveCamera({ heading: 0, tilt: 0 });
      map.fitBounds(bounds, pad);
    },
    zoomBy(d) { map.setZoom(map.getZoom() + d); },
    onUser(cb) { map.addListener("dragstart", cb); },
    resize() {},
    destroy() { g.event.clearInstanceListeners(map); container.innerHTML = ""; },
  };
}

// ---------- Contrôleur ----------
export function createNav({ $, store, settings, obdSpeed, onChange }) {
  const st = {
    engine: null, engineKey: "", mode: "idle", route: null, follow: true, tilt: store.get("nav-3d", true),
    gps: null, sim: false, along: 0, hint: 0, pos: null, heading: 0, speed: 0, shown: null, shownHeading: 0,
    fixPrev: null, fixCur: null, offCount: 0, lastReroute: 0, visible: false, arrivedAt: 0,
    recent: store.get("nav-recent2", []), dest: store.get("nav-dest2", null), msgTimer: 0,
  };
  const msg = (t, keep) => {
    $("nav-msg").textContent = t ?? "";
    clearTimeout(st.msgTimer);
    if (t && !keep) st.msgTimer = setTimeout(() => ($("nav-msg").textContent = ""), 6000);
  };

  // --- Moteur : OSM par défaut, Google si une clé est donnée et choisie ---
  async function ensureEngine() {
    const wantGoogle = settings.engine === "google" && settings.gkey;
    const key = wantGoogle ? `google|${settings.gkey}|${settings.gmapid}|${settings.night}` : "osm";
    if (st.engine && st.engineKey === key) return st.engine;
    if (st.engine && st.engineKey === "osm" && key === "osm") return st.engine;
    st.engine?.destroy();
    st.engine = null;
    const box = $("nav-map");
    box.innerHTML = "";
    try {
      st.engine = wantGoogle ? await googleEngine(box, settings.night, settings.gkey, settings.gmapid) : await osmEngine(box, settings.night);
    } catch (e) {
      msg(`${wantGoogle ? "Google Maps" : "Carte"} : ${e?.message ?? e}. ${wantGoogle ? "Retour sur OpenStreetMap." : "Vérifie la connexion Internet."}`, true);
      if (!wantGoogle) return null;
      box.innerHTML = "";
      st.engine = await osmEngine(box, settings.night).catch(() => null);
      st.engineKey = "osm";
      return st.engine;
    }
    st.engineKey = key;
    st.engine.onUser(() => { if (st.mode !== "preview") { st.follow = false; $("nt-center").hidden = false; } });
    $("nav-engine").textContent = st.engine.kind === "google" ? "Google Maps · trafic" : "OpenStreetMap";
    redraw();
    return st.engine;
  }
  function redraw() {
    const e = st.engine;
    if (!e) return;
    const r = st.route;
    if (r && st.mode === "drive") {
      const cut = pointAt(r, st.along);
      e.setRoute([cut.pos, ...r.coords.slice(cut.idx + 1)], [...r.coords.slice(0, cut.idx + 1), cut.pos]);
    } else e.setRoute(r?.coords ?? null, null);
    e.setDest(st.dest && st.mode !== "idle" ? [st.dest.lon, st.dest.lat] : null);
    if (st.mode === "preview" && r) e.fit(r.coords, { top: 60, bottom: 60, left: $("nav-preview").offsetWidth + 60, right: 90 });
  }

  // --- Position : GPS du script Python, sinon simulation pendant le guidage ---
  async function pollGps() {
    try {
      const g = await (await fetch("/api/gps")).json();
      st.gps = g;
      if (g.fix && g.lat != null) {
        const p = [g.lon, g.lat];
        if (!st.fixCur || dist(st.fixCur.p, p) > 0.3 || g.speed > 1) {
          st.fixPrev = st.fixCur ?? { p, t: performance.now() };
          st.fixCur = { p, t: performance.now() };
        }
        if (g.speed > 4 && g.heading != null) st.heading = g.heading;
        else if (st.fixPrev && dist(st.fixPrev.p, p) > 3) st.heading = bearing(st.fixPrev.p, p);
        st.speed = g.speed ?? 0;
        if (st.sim) { st.sim = false; msg("Signal GPS retrouvé : fin de la simulation."); }
      }
    } catch { st.gps = null; }
    gpsChip();
  }
  const hasFix = () => !!st.gps?.fix && !!st.fixCur;
  function gpsChip() {
    const g = st.gps, chip = $("nav-gps");
    let color = "var(--dim)", text = "Pas de GPS";
    if (st.sim) { color = "var(--accent)"; text = "Simulation"; }
    else if (g?.fix) { color = "var(--ok)"; text = `${g.source === "iphone" ? "GPS iPhone" : "GPS"}${g.sats ? ` · ${g.sats} sat.` : ""}`; }
    else if (g?.source) { color = "var(--accent)"; text = g.status; }
    chip.querySelector("i").style.background = color;
    chip.querySelector("span").textContent = text;
  }

  // --- Recherche d'adresse (Nominatim) ---
  async function search(q) {
    q = q.trim();
    if (!q) return;
    $("nav-results").innerHTML = '<div class="nav-hint">Recherche…</div>';
    const near = st.pos ?? (hasFix() ? st.fixCur.p : null);
    const view = near ? `&viewbox=${near[0] - 1},${near[1] + 1},${near[0] + 1},${near[1] - 1}` : "";
    try {
      const r = await fetch(`${NOMINATIM}?format=jsonv2&limit=6&countrycodes=fr,be,ch,lu,mc,es,it,de&accept-language=fr&addressdetails=1${view}&q=${encodeURIComponent(q)}`);
      const list = r.ok ? await r.json() : [];
      if (!list.length) { $("nav-results").innerHTML = '<div class="nav-hint">Aucun résultat.</div>'; return; }
      $("nav-results").innerHTML = list.map((h, i) => {
        const a = h.address ?? {}, title = h.name || [a.house_number, a.road].filter(Boolean).join(" ") || h.display_name.split(",")[0];
        const sub = h.display_name.split(", ").filter((x) => x !== title).slice(0, 4).join(", ");
        return `<button data-i="${i}"><b>${esc(title)}</b><span>${esc(sub)}</span></button>`;
      }).join("");
      $("nav-results").onclick = (e) => {
        const b = e.target.closest("button");
        if (!b) return;
        const h = list[+b.dataset.i], a = h.address ?? {};
        const name = h.name || [a.house_number, a.road].filter(Boolean).join(" ") || h.display_name.split(",")[0];
        go({ name, lat: +h.lat, lon: +h.lon });
      };
    } catch {
      $("nav-results").innerHTML = '<div class="nav-hint">Recherche impossible : pas de connexion Internet ?</div>';
    }
  }
  async function geocodeOne(q) {
    const r = await fetch(`${NOMINATIM}?format=jsonv2&limit=1&accept-language=fr&q=${encodeURIComponent(q)}`);
    const [h] = r.ok ? await r.json() : [];
    return h ? { name: q, lat: +h.lat, lon: +h.lon } : null;
  }

  // --- Calcul d'itinéraire (OSRM) ---
  async function origin() {
    if (hasFix()) return st.fixCur.p;
    if (st.mode === "drive" && st.pos) return st.pos;
    if (settings.start) {
      st.startGeo ??= {};
      st.startGeo[settings.start] ??= await geocodeOne(settings.start);
      const s = st.startGeo[settings.start];
      if (s) return [s.lon, s.lat];
    }
    return null;
  }
  async function computeRoute(from, dest) {
    const r = await fetch(`${OSRM}${from[0]},${from[1]};${dest.lon},${dest.lat}?overview=full&geometries=geojson&steps=true`);
    const j = await r.json();
    if (j.code !== "Ok" || !j.routes?.length) throw new Error(j.message || "aucun itinéraire");
    return buildRoute(j, dest);
  }
  async function go(dest) {
    $("nav-results").innerHTML = "";
    $("nav-q").value = "";
    $("nav-q").blur();
    st.dest = dest;
    store.set("nav-dest2", dest);
    st.recent = [dest, ...st.recent.filter((r) => r.name !== dest.name)].slice(0, 4);
    store.set("nav-recent2", st.recent);
    const from = await origin();
    if (!from) {
      msg("Position inconnue : branche le GPS, ou renseigne « Départ sans GPS » dans Paramètres.", true);
      return;
    }
    msg("Calcul de l'itinéraire…", true);
    try {
      st.route = await computeRoute(from, dest);
      msg("");
      setMode("preview");
    } catch (e) {
      msg(`Itinéraire impossible : ${e.message}.`);
    }
  }

  // --- Modes : recherche (idle), aperçu de l'itinéraire, guidage ---
  function setMode(m) {
    st.mode = m;
    $("nav-search").hidden = m !== "idle";
    $("nav-preview").hidden = m !== "preview";
    $("nav-turn").hidden = m !== "drive";
    $("nav-bottom").hidden = m !== "drive";
    if (m === "preview") {
      const r = st.route;
      $("np-dest").textContent = st.dest.name;
      $("np-sum").innerHTML = `<b>${fmtDur(r.duration)}</b> · ${fmtDist(r.total)} · arrivée ${fmtTime(new Date(Date.now() + r.duration * 1000))}`;
      $("np-open").textContent = `Ouvrir dans ${settings.nav === "waze" ? "Waze" : "Google Maps"} ↗`;
      $("np-start").textContent = hasFix() ? "Démarrer" : "Démarrer (simulation)";
    }
    if (m === "drive") { st.follow = true; $("nt-center").hidden = true; }
    if (m === "idle") { st.route = null; st.sim = false; }
    redraw();
    renderChips();
    onChange();
  }
  function start() {
    st.along = 0; st.hint = 0; st.offCount = 0;
    st.sim = !hasFix();
    if (st.sim) { const p = pointAt(st.route, 0); st.pos = p.pos; st.heading = p.heading; }
    setMode("drive");
    gpsChip();
  }
  function stop() { st.dest = null; store.set("nav-dest2", null); setMode("idle"); gpsChip(); }

  async function reroute() {
    st.lastReroute = performance.now();
    msg("Nouvel itinéraire…", true);
    try {
      st.route = await computeRoute(st.pos, st.dest);
      st.along = 0; st.hint = 0; st.offCount = 0;
      msg("");
      redraw();
    } catch { msg("Recalcul impossible pour l'instant."); }
  }

  // --- Boucle de guidage (10 fois par seconde) ---
  let last = performance.now();
  function tick() {
    const now = performance.now(), dt = Math.min(0.5, (now - last) / 1000);
    last = now;
    if (st.mode === "drive" && st.sim) {
      // Simulation : on roule à la vitesse lue sur l'OBD (ou 50 km/h), le long du tracé
      const v = obdSpeed();
      st.speed = v != null && v > 2 ? Math.min(v, 130) : v != null ? 0 : 50;
      st.along = Math.min(st.route.total, st.along + (st.speed / 3.6) * dt);
      const p = pointAt(st.route, st.along);
      st.pos = p.pos;
      st.heading = p.heading;
    } else if (hasFix()) {
      // Interpole entre les deux dernières positions GPS pour un déplacement fluide
      const { fixPrev: a, fixCur: b } = st, k = Math.min(1, (now - b.t) / 1000);
      st.pos = a ? [lerp(a.p[0], b.p[0], k), lerp(a.p[1], b.p[1], k)] : b.p;
    }
    if (st.mode === "drive" && st.route) guide(now);
    $("ns-v").textContent = st.pos ? Math.round(st.speed) : "—";
  }
  function guide(now) {
    const r = st.route;
    if (!st.sim && st.pos) {
      const loc = locate(r, st.pos, st.hint);
      st.hint = loc.idx;
      st.along = Math.max(st.along - 20, loc.along);
      st.offCount = loc.off > 45 ? st.offCount + 1 : 0;
      if (st.offCount > 25 && now - st.lastReroute > 10000) reroute();
    }
    const left = r.total - st.along;
    if (left < 25) {
      if (!st.arrivedAt) { st.arrivedAt = now; $("turn-dist").textContent = "Vous êtes arrivé"; $("turn-text").textContent = st.dest.name; $("turn-road").textContent = ""; $("turn-ico").innerHTML = icon({ maneuver: { type: "arrive" } }); $("turn-next").hidden = true; }
      if (now - st.arrivedAt > 6000) { st.arrivedAt = 0; stop(); }
      return;
    }
    const i = r.steps.findIndex((s) => s.at > st.along + 3 && s.maneuver.type !== "depart");
    const s = r.steps[i], after = r.steps[i + 1];
    if (s) {
      $("turn-dist").textContent = fmtDist(s.at - st.along);
      $("turn-text").textContent = instruction(s);
      $("turn-road").textContent = road(s);
      const ic = icon(s);
      if ($("turn-ico").dataset.k !== ic) { $("turn-ico").innerHTML = ic; $("turn-ico").dataset.k = ic; }
      const soon = after && after.at - s.at < 400;
      $("turn-next").hidden = !soon;
      if (soon) $("turn-next").innerHTML = `Puis ${icon(after)} ${esc(instruction(after).toLowerCase())}`;
    }
    const remain = r.duration * (left / r.total);
    $("nb-eta").textContent = fmtTime(new Date(Date.now() + remain * 1000));
    $("nb-time").textContent = fmtDur(remain);
    $("nb-km").textContent = fmtDist(left);
    st.tile = s ? `Dans ${fmtDist(s.at - st.along)} · ${instruction(s)}` : null;
    if (now - (st.lastRedraw ?? 0) > 700) { st.lastRedraw = now; redraw(); }
  }
  setInterval(tick, 100);
  setInterval(() => { pollGps(); if (st.mode === "drive") onChange(); }, 1000);
  pollGps();

  // --- Caméra (à chaque image quand la carte est visible) ---
  function frame() {
    requestAnimationFrame(frame);
    const e = st.engine;
    if (!st.visible || !e || !st.pos) { e?.setCar(st.pos, st.heading); return; }
    const k = 0.18;
    st.shown = st.shown ? [lerp(st.shown[0], st.pos[0], k), lerp(st.shown[1], st.pos[1], k)] : st.pos;
    st.shownHeading = lerpAngle(st.shownHeading, st.heading, 0.12);
    e.setCar(st.shown, st.shownHeading);
    if (!st.follow || st.mode === "preview") return;
    const drive = st.mode === "drive", h = $("nav-map").clientHeight;
    const zoom = drive ? (st.speed > 90 ? 15 : st.speed > 50 ? 15.8 : 16.6) : 15.5;
    e.view({ center: st.shown, bearing: drive ? st.shownHeading : 0, pitch: drive && st.tilt ? 58 : 0, zoom,
             offset: drive ? h * 0.42 : 0 });
  }
  requestAnimationFrame(frame);

  // --- Raccourcis : Domicile, Travail, récents ---
  function renderChips() {
    const fav = [["Domicile", settings.home], ["Travail", settings.work]].filter(([, a]) => a?.trim());
    $("nav-chips").innerHTML = fav.map(([k, a]) => `<button data-fav="${esc(a)}"><b>${k}</b>${esc(a)}</button>`).join("")
      + st.recent.map((r, i) => `<button data-recent="${i}">${esc(r.name)}</button>`).join("");
  }
  $("nav-chips").onclick = async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.recent) return go(st.recent[+b.dataset.recent]);
    msg("Recherche de l'adresse…", true);
    const d = await geocodeOne(b.dataset.fav).catch(() => null);
    if (d) { msg(""); go({ ...d, name: b.querySelector("b").textContent }); } else msg("Adresse introuvable.");
  };

  // --- Commandes ---
  $("nav-form").onsubmit = (e) => { e.preventDefault(); search($("nav-q").value); };
  $("np-start").onclick = start;
  $("np-cancel").onclick = stop;
  $("np-open").onclick = () => {
    const d = st.dest, from = st.pos ?? (hasFix() ? st.fixCur.p : null);
    window.open(settings.nav === "waze"
      ? `https://www.waze.com/ul?ll=${d.lat},${d.lon}&navigate=yes`
      : `https://www.google.com/maps/dir/?api=1${from ? `&origin=${from[1]},${from[0]}` : ""}&destination=${d.lat},${d.lon}&travelmode=driving`, "_blank", "noopener");
  };
  $("nb-stop").onclick = stop;
  $("nb-overview").onclick = () => { st.follow = false; $("nt-center").hidden = false; st.engine?.fit(st.route.coords, { top: 160, bottom: 120, left: 60, right: 60 }); };
  $("nt-center").onclick = () => { st.follow = true; $("nt-center").hidden = true; };
  $("nt-in").onclick = () => { st.follow = false; $("nt-center").hidden = false; st.engine?.zoomBy(1); };
  $("nt-out").onclick = () => { st.follow = false; $("nt-center").hidden = false; st.engine?.zoomBy(-1); };
  $("nt-3d").onclick = () => { st.tilt = !st.tilt; store.set("nav-3d", st.tilt); $("nt-3d").textContent = st.tilt ? "2D" : "3D"; };
  $("nt-3d").textContent = st.tilt ? "2D" : "3D";

  return {
    async show() {
      st.visible = true;
      renderChips();
      const e = await ensureEngine();
      e?.resize();
      if (st.mode === "idle" && !st.pos) {
        const from = await origin().catch(() => null);
        if (from && !st.pos) st.engine?.view({ center: from, bearing: 0, pitch: 0, zoom: 12, offset: 0 });
      }
    },
    hide() { st.visible = false; },
    // Réglages changés : moteur ou thème de carte
    async settingsChanged() {
      if (!st.engine) return;
      if (st.engine.kind === "osm" && st.engineKey === "osm" && !(settings.engine === "google" && settings.gkey)) st.engine.setNight(settings.night);
      if (st.visible) await ensureEngine();
      else { st.engine.destroy(); st.engine = null; st.engineKey = ""; }
      if (st.mode === "preview") setMode("preview");
    },
    tile() {
      if (st.mode === "drive") return { k: st.sim ? "Guidage · simulation" : "Guidage", title: st.tile ?? st.dest.name, sub: `Arrivée ${$("nb-eta").textContent} · ${$("nb-km").textContent}` };
      if (st.mode === "preview") return { k: "Itinéraire", title: st.dest.name, sub: "Prêt à démarrer" };
      return { k: "Navigation", title: "Où allez-vous ?", sub: settings.engine === "google" && settings.gkey ? "Google Maps" : "OpenStreetMap" };
    },
  };
}

const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
