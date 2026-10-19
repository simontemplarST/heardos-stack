// HeardOS desktop. Each app opens in a window as an <iframe> of its own address. All apps share the family domain, so
// the browser sends the Authentik session inside the frame: people are already signed in. The lighthouse Caddy lets
// only this page (and each app itself) frame the apps; see docs/web-desktop.md.
//
// Layout of this file: helpers · state · boot · session/offline · icons · windows · taskbar/pins · menus (Start, account,
// right-click) · widgets · native windows (Settings, Help, System, Welcome) · session restore · phone back button · PWA.
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {   // localStorage can throw (private mode, blocked storage): the desktop must work without it
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
const IFRAME_ALLOW = "fullscreen; clipboard-write; publickey-credentials-get";   // apps.json `allow` overrides per app
const isPhone = () => matchMedia("(max-width: 760px)").matches;
const prefersDark = matchMedia("(prefers-color-scheme: dark)");

const S = { me: null, apps: [], folders: [], links: {}, support: {}, version: "", admin: false,
  wins: new Map(), z: 10, active: null, seq: 0, prefs: {}, online: true, locked: false };
const KEY = () => `heardos.desktop.${S.me?.username || "anon"}`;
const DEFAULT_PREFS = { theme: "auto", wallpaper: "hearth", icons: "m", text: "m", widgets: true, restore: true, pinned: [], recent: [], welcomed: false };

// ---------------------------------------------------------------- talking to the server
// fetch with redirect:"manual": when the Authentik session has expired, the gate answers with a redirect to the
// sign-in page, which shows up here as an "opaqueredirect" instead of a confusing network error.
class SignedOut extends Error {}
async function api(path, opts = {}) {
  const r = await fetch(path, { cache: "no-store", redirect: "manual", ...opts });
  if (r.type === "opaqueredirect" || r.status === 401) throw new SignedOut("signed out");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const ct = r.headers.get("content-type") || "";
  if (!ct.includes("json")) throw new SignedOut("signed out");   // an HTML sign-in page instead of JSON
  return r.json();
}

// ---------------------------------------------------------------- boot
async function boot() {
  try {
    const d = await api("/api/apps");
    Object.assign(S, { apps: d.apps, folders: d.folders, links: d.links, support: d.support || {}, version: d.version || "", admin: !!d.admin });
    S.me = await api("/api/me");
  } catch (e) {
    if (e instanceof SignedOut) return lock("signed-out");
    return lock("unreachable", e);
  }
  S.prefs = { ...DEFAULT_PREFS, ...store.get(KEY() + ".prefs", {}) };
  if (S.admin) S.apps.push({ id: "system", name: "System", description: "How the desktop and its data sources are doing (admins)", folder: "Admin", icon: "/icons/system.svg", url: "", embed: true, native: "system" });
  applyPrefs();
  renderUser(); renderIcons(); renderPins(); renderStartList(""); clock(); setInterval(clock, 15000);
  widgets(); setInterval(widgets, 5 * 60 * 1000);
  if (S.prefs.restore) restoreSession();
  const want = new URLSearchParams(location.search).get("open");   // /?open=<app id> (launcher shortcuts)
  if (want && appById(want)) openApp(want);
  if (!S.prefs.welcomed) welcome();
  setInterval(checkSession, 4 * 60 * 1000);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") checkSession(); });
}

// ---------------------------------------------------------------- session expiry and connectivity
// The desktop only knows the session is gone when a request fails; apps inside windows would otherwise just show a
// sign-in page in a small frame. So: check on a timer, when the tab comes back, and whenever a request fails.
async function checkSession() {
  if (S.locked) return;
  try { await api("/api/me"); setOnline(true); }
  catch (e) { if (e instanceof SignedOut) lock("signed-out"); else setOnline(false); }
}
function lock(kind, err) {
  S.locked = true;
  const L = $("#lock"), t = $("#lock-title"), p = $("#lock-text"), b = $("#lock-btn"), d = $("#lock-dismiss");
  if (kind === "signed-out") {
    t.textContent = "You've been signed out"; p.textContent = "Your sign-in has expired or you signed out somewhere else. Sign in again to carry on where you left off; your windows are remembered.";
    b.textContent = "Sign in again"; b.onclick = () => { save(); location.reload(); }; d.hidden = true;
  } else {
    t.textContent = "Can't reach home right now"; p.textContent = `The desktop can't reach the home server${navigator.onLine ? "" : " (this device looks offline)"}. It keeps trying; you can also retry now.`;
    b.textContent = "Try again"; b.onclick = () => { S.locked = false; L.hidden = true; S.me ? checkSession() : boot(); }; d.hidden = !S.me;
    d.onclick = () => { S.locked = false; L.hidden = true; setOnline(false); };
    if (!S.me) setTimeout(() => { if (S.locked) b.onclick(); }, 15000);   // nothing loaded yet: keep retrying by itself
  }
  L.hidden = false; b.focus();
}
function setOnline(on) {
  const st = $("#status");
  if (on && !S.online) { st.textContent = "Back online."; st.className = "ok"; st.hidden = false; setTimeout(() => { if (S.online) st.hidden = true; }, 3000); widgets(); }
  if (!on) { st.textContent = navigator.onLine ? "Can't reach home right now. Retrying…" : "You're offline. Apps will come back when the connection does."; st.className = ""; st.hidden = false; }
  S.online = on;
}
addEventListener("online", () => checkSession());
addEventListener("offline", () => setOnline(false));

// ---------------------------------------------------------------- user, clock, toasts
const initials = (n) => (n || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
const firstName = () => (S.me?.name || S.me?.username || "").split(" ")[0] || "there";
function renderUser() {
  const n = S.me.name || S.me.username || "Signed in";
  $("#user").textContent = initials(n); $("#user").title = `${n} — account`;
  $("#sm-avatar").textContent = initials(n); $("#sm-name").textContent = n; $("#sm-email").textContent = S.me.email || "";
}
function clock() {
  const d = new Date();
  $("#clock").innerHTML = `${d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}<br><span class="muted">${d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}</span>`;
}
function toast(html, { ms = 7000, bad = false } = {}) {
  const t = document.createElement("div"); t.className = "toast" + (bad ? " bad" : "");
  t.innerHTML = html + `<button class="x" aria-label="Dismiss">✕</button>`;
  $(".x", t).addEventListener("click", () => t.remove());
  $("#toasts").append(t); if (ms) setTimeout(() => t.remove(), ms);
}
// an app icon that fails to load (wrong name in apps.json, file missing from the image) becomes a lettered tile
function iconEl(app, cls = "") {
  const img = document.createElement("img"); img.src = app.icon; img.alt = ""; if (cls) img.className = cls;
  img.addEventListener("error", () => { const t = document.createElement("div"); t.className = "tile " + cls; t.textContent = (app.name || "?")[0].toUpperCase(); img.replaceWith(t); }, { once: true });
  return img;
}

// ---------------------------------------------------------------- desktop icons
function renderIcons() {
  const box = $("#icons"); box.innerHTML = "";
  const folders = [...S.folders]; if (!folders.includes("Admin") && S.apps.some((a) => a.folder === "Admin")) folders.push("Admin");
  for (const f of folders) {
    for (const a of S.apps.filter((x) => x.folder === f)) {
      const b = document.createElement("button");
      b.className = "dicon"; b.dataset.app = a.id; b.title = a.description || a.name; b.setAttribute("role", "listitem");
      b.append(iconEl(a)); const s = document.createElement("span"); s.textContent = a.name; b.append(s);
      b.addEventListener("click", (e) => {   // mouse: select, double-click opens · touch/pen: one tap opens
        $$(".dicon.sel").forEach((x) => x.classList.remove("sel")); b.classList.add("sel");
        const touch = e.pointerType ? e.pointerType !== "mouse" : matchMedia("(pointer: coarse)").matches;
        if (touch && e.detail) openApp(a.id);   // e.detail = 0 for keyboard "clicks" (Enter is handled below)
      });
      b.addEventListener("dblclick", () => openApp(a.id));
      b.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openApp(a.id); } else if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) { e.preventDefault(); const r = b.getBoundingClientRect(); contextMenu(a, r.left + 20, r.top + 20); } });
      b.addEventListener("contextmenu", (e) => { e.preventDefault(); contextMenu(a, e.clientX, e.clientY); });
      longPress(b, (x, y) => contextMenu(a, x, y));
      box.append(b);
    }
  }
}
$("#desktop").addEventListener("pointerdown", (e) => { if (e.target.id === "desktop" || e.target.id === "icons") $$(".dicon.sel").forEach((x) => x.classList.remove("sel")); });
function longPress(el, fn) {   // touch: hold half a second for the menu
  let t;
  el.addEventListener("pointerdown", (e) => { if (e.pointerType === "mouse") return; t = setTimeout(() => { t = null; fn(e.clientX, e.clientY); }, 500); });
  for (const ev of ["pointerup", "pointercancel", "pointermove"]) el.addEventListener(ev, (e) => { if (ev === "pointermove" && t && e.movementX ** 2 + e.movementY ** 2 < 25) return; clearTimeout(t); });
}

// ---------------------------------------------------------------- windows
function appById(id) { return S.apps.find((a) => a.id === id); }
function appForUrl(u) {   // the app whose address this URL is on (longest prefix wins)
  let best = null;
  for (const a of S.apps) {
    if (!a.url) continue;
    const base = new URL(a.url, location.href), t = new URL(u, location.href);
    if (base.origin === t.origin && (!best || base.pathname.length > new URL(best.url, location.href).pathname.length)) best = a;
  }
  return best;
}
function noteRecent(id) {
  S.prefs.recent = [id, ...S.prefs.recent.filter((x) => x !== id)].slice(0, 6); store.set(KEY() + ".prefs", S.prefs);
}
function openApp(id, url) {
  if (id === "settings") return openSettings();
  if (id === "help") return openHelp();
  const app = appById(id); if (!app) return;
  closeMenus();
  if (app.native === "system") return openSystem();
  noteRecent(id);
  if (!app.embed) { window.open(url || app.url, "_blank", "noopener"); return; }
  const open = [...S.wins.values()].find((w) => w.app?.id === id);
  if (open) { if (url) open.iframe.src = url; restore(open); focus(open); return open; }
  return createWindow({ app, url: url || (app.id === "week" ? `${app.url}?theme=${resolvedTheme()}` : app.url) });
}
function defaultRect(app) {
  const D = $("#desktop").getBoundingClientRect(), n = S.wins.size % 8;
  const w = Math.min(app?.w || 1180, D.width - 40), h = Math.min(app?.h || 780, D.height - 30);
  return { x: Math.max(10, Math.round((D.width - w) / 2 - 120 + n * 32)), y: Math.max(8, 20 + n * 28), w, h };
}
function createWindow({ app, url, rect, max, min, native }) {
  const el = $("#tpl-window").content.firstElementChild.cloneNode(true);
  const id = `w${++S.seq}`;
  const w = { id, app, el, iframe: null, max: false, min: false, rect: rect || defaultRect(app), native: !!native };
  el.dataset.win = id;
  $(".ticon", el).src = app.icon; $(".ttitle", el).textContent = app.name; el.setAttribute("aria-label", app.name);
  const body = $(".wbody", el), state = $(".tstate", el);
  if (native) {
    body.classList.add("native"); $(".loading", el).classList.add("done");
    $$('.tbtns [data-act="reload"], .tbtns [data-act="popout"]', el).forEach((b) => b.remove());
    body.append(native);
  } else {
    const f = document.createElement("iframe");
    f.title = app.name; f.allow = typeof app.allow === "string" ? app.allow : IFRAME_ALLOW; f.referrerPolicy = "strict-origin-when-cross-origin";
    f.src = url; w.iframe = f; body.append(f);
    const ld = $(".loading", el), slow = $(".slow", el);
    state.textContent = "loading…";
    w.loadTimer = setTimeout(() => { slow.hidden = false; state.textContent = "slow to load"; }, 15000);
    f.addEventListener("load", () => { clearTimeout(w.loadTimer); ld.classList.add("done"); state.textContent = ""; $(`.task[data-win="${id}"]`)?.classList.remove("loading"); });
  }
  wireWindow(w);
  S.wins.set(id, w);
  $("#desktop").append(el);
  place(w);
  if (max) toggleMax(w, true);
  el.classList.add("opening"); setTimeout(() => el.classList.remove("opening"), 200);
  addTask(w);
  if (min) minimize(w); else focus(w);
  if (isPhone()) pushPhoneHistory(w);
  save();
  return w;
}
function place(w) { const r = w.rect, s = w.el.style; s.left = r.x + "px"; s.top = r.y + "px"; s.width = r.w + "px"; s.height = r.h + "px"; }
function focus(w) {
  if (!w || w.min) return;
  S.active = w.id; w.el.style.zIndex = ++S.z;
  $$(".win").forEach((x) => x.classList.toggle("active", x === w.el));
  $$(".task").forEach((t) => { const on = t.dataset.win === w.id; t.classList.toggle("active", on); t.setAttribute("aria-selected", on); });
}
function topVisible() { return [...S.wins.values()].filter((x) => !x.min).sort((a, b) => b.el.style.zIndex - a.el.style.zIndex)[0]; }
function minimize(w) {
  w.min = true; w.el.classList.add("min");
  $(`.task[data-win="${w.id}"]`)?.classList.remove("active");
  if (S.active === w.id) { S.active = null; focus(topVisible()); }
  save();
}
function restore(w) { if (w.min) { w.min = false; w.el.classList.remove("min"); } focus(w); save(); }
function toggleMax(w, on = !w.max) {
  const D = $("#desktop").getBoundingClientRect();
  if (on && !w.max) { w.prev = { ...w.rect }; w.rect = { x: 0, y: 0, w: D.width, h: D.height }; }
  else if (!on && w.max) w.rect = w.prev || defaultRect(w.app);
  w.max = on; w.el.classList.toggle("max", on); place(w); save();
}
function closeWin(w, fromHistory = false) {
  clearTimeout(w.loadTimer);
  w.el.remove(); $(`.task[data-win="${w.id}"]`)?.remove(); S.wins.delete(w.id);
  if (S.active === w.id) { S.active = null; focus(topVisible()); }
  if (!fromHistory && isPhone() && history.state?.heardosWin === w.id) { S.ignorePop = true; history.back(); }
  save();
}
function reloadWin(w) {
  if (!w.iframe) return;
  $(".loading", w.el).classList.remove("done"); $(".slow", w.el).hidden = true; $(".tstate", w.el).textContent = "loading…";
  clearTimeout(w.loadTimer); w.loadTimer = setTimeout(() => { $(".slow", w.el).hidden = false; }, 15000);
  w.iframe.src = w.iframe.src;
}
function wireWindow(w) {
  const el = w.el;
  el.addEventListener("pointerdown", () => focus(w), true);
  el.addEventListener("click", (e) => {
    const act = e.target.closest("button[data-act]")?.dataset.act; if (!act) return;
    if (act === "close") closeWin(w);
    else if (act === "min") minimize(w);
    else if (act === "max") toggleMax(w);
    else if (act === "reload") reloadWin(w);
    else if (act === "popout") window.open(w.iframe.src, "_blank", "noopener");
  });
  const tb = $(".titlebar", el);
  tb.addEventListener("dblclick", (e) => { if (!e.target.closest("button") && !isPhone()) toggleMax(w); });
  tb.addEventListener("pointerdown", (e) => { if (!e.target.closest("button") && e.button === 0 && !isPhone()) drag(w, e); });
  // keyboard: with the title bar focused, arrows move, Shift+arrows resize, Enter maximises, Delete closes, M minimises
  tb.addEventListener("keydown", (e) => {
    if (e.target !== tb) return;
    const step = e.ctrlKey ? 1 : 24, r = { ...w.rect };
    const k = e.key;
    if (k.startsWith("Arrow")) {
      if (w.max) return;
      const dx = k === "ArrowLeft" ? -step : k === "ArrowRight" ? step : 0, dy = k === "ArrowUp" ? -step : k === "ArrowDown" ? step : 0;
      if (e.shiftKey) { r.w = Math.max(320, r.w + dx); r.h = Math.max(200, r.h + dy); } else { r.x += dx; r.y = Math.max(0, r.y + dy); }
      w.rect = r; place(w); save(); e.preventDefault();
    } else if (k === "Enter") { toggleMax(w); e.preventDefault(); }
    else if (k === "Delete" || k === "Backspace") { closeWin(w); e.preventDefault(); }
    else if (k.toLowerCase() === "m") { minimize(w); e.preventDefault(); }
  });
  $$(".rz", el).forEach((h) => h.addEventListener("pointerdown", (e) => { if (e.button === 0) resize(w, e, [...h.classList].find((c) => c !== "rz")); }));
}

// drag with edge snapping: left/right edge = half screen, top edge = maximise
function drag(w, e) {
  e.preventDefault();
  const D = $("#desktop").getBoundingClientRect(), pv = $("#snap-preview");
  let start = { x: e.clientX, y: e.clientY }, r0 = { ...w.rect }, snap = null, moved = false;
  document.body.classList.add("dragging");
  const move = (ev) => {
    if (!moved && Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 4) return;
    if (!moved && w.max) {   // pulling a maximised window off the top restores it under the pointer
      const keep = w.prev || defaultRect(w.app); w.max = false; w.el.classList.remove("max");
      r0 = { ...keep, x: ev.clientX - D.left - keep.w * ((start.x - D.left) / D.width), y: 0 }; start = { x: ev.clientX, y: ev.clientY };
    }
    moved = true;
    const x = r0.x + ev.clientX - start.x, y = Math.max(0, Math.min(D.height - 38, r0.y + ev.clientY - start.y));
    w.rect = { ...w.rect, x, y, w: r0.w, h: r0.h }; place(w);
    const px = ev.clientX - D.left, py = ev.clientY - D.top;
    snap = py < 6 ? "max" : px < 8 ? "left" : px > D.width - 8 ? "right" : null;
    if (snap) { const s = snapRect(snap, D); Object.assign(pv.style, { left: s.x + "px", top: s.y + "px", width: s.w + "px", height: s.h + "px" }); pv.hidden = false; }
    else pv.hidden = true;
  };
  const up = () => {
    removeEventListener("pointermove", move); removeEventListener("pointerup", up); removeEventListener("pointercancel", up);
    document.body.classList.remove("dragging"); pv.hidden = true;
    if (snap === "max") { w.prev = { ...r0 }; toggleMax(w, true); }
    else if (snap) { w.prev = { ...r0 }; w.rect = snapRect(snap, D); place(w); }
    save();
  };
  addEventListener("pointermove", move); addEventListener("pointerup", up); addEventListener("pointercancel", up);
}
const snapRect = (side, D) => side === "max" ? { x: 0, y: 0, w: D.width, h: D.height }
  : { x: side === "left" ? 0 : Math.round(D.width / 2), y: 0, w: Math.round(D.width / 2), h: D.height };
function resize(w, e, dir) {
  e.preventDefault(); e.stopPropagation();
  const s = { x: e.clientX, y: e.clientY }, r0 = { ...w.rect }, MINW = 320, MINH = 200;
  document.body.classList.add("dragging");
  const move = (ev) => {
    const dx = ev.clientX - s.x, dy = ev.clientY - s.y, r = { ...r0 };
    if (dir.includes("e")) r.w = Math.max(MINW, r0.w + dx);
    if (dir.includes("s")) r.h = Math.max(MINH, r0.h + dy);
    if (dir.includes("w")) { r.w = Math.max(MINW, r0.w - dx); r.x = r0.x + r0.w - r.w; }
    if (dir.includes("n")) { r.h = Math.max(MINH, r0.h - dy); r.y = Math.max(0, r0.y + r0.h - r.h); }
    w.rect = r; place(w);
  };
  const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); document.body.classList.remove("dragging"); save(); };
  addEventListener("pointermove", move); addEventListener("pointerup", up);
}
function tidy() {   // cascade every open window
  [...S.wins.values()].forEach((w, i) => { w.min = false; w.el.classList.remove("min", "max"); w.max = false; w.rect = { ...defaultRect(w.app), x: 30 + i * 34, y: 16 + i * 30 }; place(w); focus(w); });
  save();
}
function cycleWindows(back = false) {   // Ctrl+` (and Ctrl+Shift+`): bring the next window to the front
  const vis = [...S.wins.values()].filter((w) => !w.min).sort((a, b) => a.el.style.zIndex - b.el.style.zIndex);
  if (vis.length < 2) return;
  focus(back ? vis[vis.length - 2] : vis[0]);
}

// ---------------------------------------------------------------- taskbar + pinned apps
function addTask(w) {
  const b = document.createElement("button");
  b.className = "task" + (w.iframe ? " loading" : ""); b.dataset.win = w.id; b.setAttribute("role", "tab"); b.title = w.app.name;
  b.append(iconEl(w.app)); const s = document.createElement("span"); s.textContent = w.app.name; b.append(s);
  b.addEventListener("click", () => { if (w.min) restore(w); else if (S.active === w.id) minimize(w); else focus(w); });
  b.addEventListener("auxclick", (e) => { if (e.button === 1) closeWin(w); });   // middle-click closes, like a browser tab
  b.addEventListener("contextmenu", (e) => { e.preventDefault(); contextMenu(w.app, e.clientX, e.clientY, w); });
  longPress(b, (x, y) => contextMenu(w.app, x, y, w));
  $("#tasks").append(b);
}
function renderPins() {
  const box = $("#pins"); box.innerHTML = "";
  for (const id of S.prefs.pinned) {
    const a = appById(id); if (!a) continue;
    const b = document.createElement("button"); b.className = "pin"; b.title = `${a.name} — ${a.description || "open"}`; b.setAttribute("aria-label", a.name);
    b.append(iconEl(a)); b.addEventListener("click", () => openApp(a.id));
    b.addEventListener("contextmenu", (e) => { e.preventDefault(); contextMenu(a, e.clientX, e.clientY); });
    box.append(b);
  }
}
function togglePin(id) {
  const on = S.prefs.pinned.includes(id);
  S.prefs.pinned = on ? S.prefs.pinned.filter((x) => x !== id) : [...S.prefs.pinned, id];
  store.set(KEY() + ".prefs", S.prefs); renderPins();
  toast(on ? "Unpinned from the taskbar." : "Pinned to the taskbar. Right-click or hold an icon to change pins.", { ms: 4000 });
}

// ---------------------------------------------------------------- menus: Start, account, right-click
function closeMenus() { $("#start-menu").hidden = true; $("#user-menu").hidden = true; $("#ctx").hidden = true; $("#start").classList.remove("open"); }
function toggleStart(force) {
  const m = $("#start-menu"), show = force ?? m.hidden;
  closeMenus();
  if (show) { m.hidden = false; $("#start").classList.add("open"); $("#sm-search").value = ""; renderStartList(""); setTimeout(() => $("#sm-search").focus(), 0); }
}
function smApp(a) {
  const b = document.createElement("button"); b.className = "sm-app"; b.dataset.app = a.id; b.setAttribute("role", "option");
  b.append(iconEl(a));
  const d = document.createElement("div"); d.innerHTML = `<div>${esc(a.name)}</div><div class="d">${esc(a.description || "")}</div>`; b.append(d);
  if (!a.embed) { const x = document.createElement("span"); x.className = "ext"; x.textContent = "↗"; x.title = "Opens in a browser tab"; b.append(x); }
  return b;
}
function renderStartList(q) {
  q = q.trim().toLowerCase();
  const words = q.split(/\s+/).filter(Boolean);
  const hit = (a) => !words.length || words.every((w) => `${a.name} ${a.description} ${a.folder}`.toLowerCase().includes(w));
  const list = $("#sm-list"); list.innerHTML = "";
  const section = (title, apps) => { if (!apps.length) return; const h = document.createElement("div"); h.className = "sm-folder"; h.textContent = title; list.append(h); apps.forEach((a) => list.append(smApp(a))); };
  if (!q) section("Recent", S.prefs.recent.map(appById).filter(Boolean).slice(0, 5));
  const folders = [...S.folders]; if (!folders.includes("Admin") && S.apps.some((a) => a.folder === "Admin")) folders.push("Admin");
  for (const f of folders) section(f, S.apps.filter((a) => a.folder === f && hit(a)));
  if (!list.children.length) list.innerHTML = `<p class="muted" style="padding:8px">No app matches “${esc(q)}”. Try another word, or press <kbd>F1</kbd> for help.</p>`;
  $(".sm-app", list)?.classList.add("hl");
}
$("#start").addEventListener("click", () => toggleStart());
$("#sm-search").addEventListener("input", (e) => renderStartList(e.target.value));
$("#sm-search").addEventListener("keydown", (e) => {
  const items = $$(".sm-app"), i = items.findIndex((x) => x.classList.contains("hl"));
  if (e.key === "Enter" && items[Math.max(0, i)]) openApp(items[Math.max(0, i)].dataset.app);
  else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault(); const n = Math.min(items.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)));
    items.forEach((x, k) => x.classList.toggle("hl", k === n)); items[n]?.scrollIntoView({ block: "nearest" });
  }
});
$("#sm-list").addEventListener("click", (e) => { const b = e.target.closest(".sm-app"); if (b) openApp(b.dataset.app); });
$("#sm-list").addEventListener("contextmenu", (e) => { const b = e.target.closest(".sm-app"); if (!b) return; e.preventDefault(); contextMenu(appById(b.dataset.app), e.clientX, e.clientY); });
$("#user").addEventListener("click", () => { const m = $("#user-menu"), show = m.hidden; closeMenus(); m.hidden = !show; });
$("#help-btn").addEventListener("click", () => openHelp());
document.addEventListener("click", (e) => {
  const act = e.target.closest("[data-action]")?.dataset.action; if (!act) return;
  closeMenus();
  if (act === "settings") openSettings();
  else if (act === "help") openHelp();
  else if (act === "account") { if (appById("account")) openApp("account"); else window.open(S.links.account, "_blank", "noopener"); }
  else if (act === "arrange") tidy();
  else if (act === "minimize-all") S.wins.forEach((w) => !w.min && minimize(w));
  else if (act === "signout") { save(); location.href = S.links.signOut; }
});
// right-click / long-press menu for an app (desktop icon, Start entry, pinned button, taskbar button)
function contextMenu(app, x, y, win) {
  closeMenus();
  const m = $("#ctx"); m.innerHTML = "";
  const item = (label, fn, cls = "") => { const b = document.createElement("button"); b.textContent = label; b.className = cls; b.setAttribute("role", "menuitem"); b.addEventListener("click", () => { closeMenus(); fn(); }); m.append(b); };
  const hr = () => m.append(document.createElement("hr"));
  if (win) { item(win.min ? "Show" : "Minimise", () => (win.min ? restore(win) : minimize(win))); if (win.iframe) { item("Reload", () => reloadWin(win)); item("Open in a browser tab", () => window.open(win.iframe.src, "_blank", "noopener")); } item("Close", () => closeWin(win), "danger"); hr(); }
  else { item("Open", () => openApp(app.id)); if (app.url) item("Open in a browser tab", () => window.open(app.url, "_blank", "noopener")); hr(); }
  if (!app.native && appById(app.id)) item(S.prefs.pinned.includes(app.id) ? "Unpin from taskbar" : "Pin to taskbar", () => togglePin(app.id));
  const d = document.createElement("div"); d.className = "d"; d.textContent = app.description || app.name; m.append(d);
  m.hidden = false;
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, innerWidth - r.width - 8) + "px"; m.style.top = Math.min(y, innerHeight - r.height - 8) + "px";
  $("button", m)?.focus();
}
document.addEventListener("pointerdown", (e) => { if (!e.target.closest("#start-menu, #start, #user-menu, #user, #ctx")) closeMenus(); });
addEventListener("blur", () => setTimeout(closeMenus, 0));   // clicking into an app window (iframe) closes menus
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
  if ((e.ctrlKey && e.code === "Space") || (e.key === "/" && !typing)) { e.preventDefault(); toggleStart(); }
  else if (e.key === "F1") { e.preventDefault(); openHelp(); }
  else if (e.key === "Escape") { closeMenus(); }
  else if (e.ctrlKey && e.code === "Backquote") { e.preventDefault(); cycleWindows(e.shiftKey); }
});
// week.html (and any same-origin page in a window) can ask to open another app
// only https: addresses (or this desktop's own pages) are opened; javascript:, data:, blob: etc. are dropped
addEventListener("message", (e) => {
  if (e.origin !== location.origin || e.data?.type !== "heardos:open-url" || typeof e.data.url !== "string") return;
  let u; try { u = new URL(e.data.url, location.href); } catch { return; }
  if (u.protocol !== "https:" && u.origin !== location.origin) return;
  const a = appForUrl(u.href);
  if (a) openApp(a.id, u.href); else window.open(u.href, "_blank", "noopener");
});

// ---------------------------------------------------------------- widgets (from /api/summary, same data as This week)
const money = (n) => (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
const greeting = () => { const h = new Date().getHours(); return h < 5 ? "Up late" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; };
async function widgets() {
  const box = $("#widgets");
  if (!S.prefs.widgets || !appById("week")) { document.body.classList.add("no-widgets"); return; }
  document.body.classList.remove("no-widgets");
  const d = new Date();
  let html = `<div class="widget" data-open="week" tabindex="0"><h3>${esc(d.toLocaleDateString(undefined, { weekday: "long" }))}</h3><div class="big">${esc(d.toLocaleDateString(undefined, { month: "long", day: "numeric" }))}</div><div class="muted small">${esc(greeting())}, ${esc(firstName())}</div></div>`;
  try {
    const s = await api("/api/summary");   // sections the person can't see are {error: "no access"} and simply get no widget
    const today = d.toISOString().slice(0, 10), m = s.mealie || {}, a = s.actual || {}, h = s.homebox || {};
    const off = (label, why) => `<div class="widget off"><h3>${label}</h3><span class="muted small">${why}</span></div>`;
    if (!m.error) {
      const meals = (m.meals || []).filter((x) => x.date >= today).slice(0, 4);
      html += `<div class="widget" data-open="recipes" tabindex="0"><h3>Meals</h3>${meals.length ? `<ul>${meals.map((x) => `<li><b>${x.date === today ? "Today" : esc(new Date(x.date + "T12:00").toLocaleDateString(undefined, { weekday: "short" }))}</b>${esc(x.title)}</li>`).join("")}</ul>` : '<span class="muted">Nothing planned yet — open Recipes to plan dinners.</span>'}</div>`;
      const open = (m.shopping || []).reduce((n, l) => n + l.open.length, 0);
      html += `<div class="widget" data-open="recipes" tabindex="0"><h3>Shopping</h3><div class="big">${open}</div><div class="muted small">item${open === 1 ? "" : "s"} to buy</div></div>`;
    } else if (m.error === "unavailable") html += off("Meals", "Recipes isn't answering right now.");
    if (!a.error && a.month) html += `<div class="widget" data-open="budget" tabindex="0"><h3>Budget · ${esc(a.month)}</h3><ul><li><b>Spent</b>${money(Math.abs(a.totalSpent))}</li><li><b>Left</b><span class="${a.toBudget < 0 ? "neg" : "pos"}">${money(a.toBudget)}</span> to budget</li></ul></div>`;
    else if (a.error === "unavailable") html += off("Budget", "The budget isn't answering right now.");
    if (!h.error && h.totals) html += `<div class="widget" data-open="inventory" tabindex="0"><h3>At home</h3><ul><li><b>${esc(h.totals.items)}</b>things in ${esc(h.totals.locations)} places</li></ul></div>`;
    setOnline(true);
  } catch (e) {
    if (e instanceof SignedOut) return lock("signed-out");
    setOnline(false);
  }
  box.innerHTML = html;
}
$("#widgets").addEventListener("click", (e) => { const w = e.target.closest("[data-open]"); if (w) openApp(appById(w.dataset.open) ? w.dataset.open : "week"); });
$("#widgets").addEventListener("keydown", (e) => { const w = e.target.closest("[data-open]"); if (w && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); w.click(); } });

// ---------------------------------------------------------------- native windows: Settings, Help, System, Welcome
function resolvedTheme() { return S.prefs.theme === "auto" ? (prefersDark.matches ? "dark" : "light") : S.prefs.theme; }
function applyPrefs() {
  document.body.dataset.wallpaper = S.prefs.wallpaper;
  document.body.dataset.theme = resolvedTheme();
  document.body.className = document.body.className.replace(/\b(icons|text)-\w+/g, "").trim() + ` icons-${S.prefs.icons} text-${S.prefs.text}`;
  $('meta[name="theme-color"]').content = resolvedTheme() === "dark" ? "#1f2023" : "#f3f1ec";
  for (const w of S.wins.values()) if (w.app.id === "week" && w.iframe) { const u = new URL(w.iframe.src, location.href); if (u.searchParams.get("theme") !== resolvedTheme()) { u.searchParams.set("theme", resolvedTheme()); w.iframe.src = u.href; } }
}
prefersDark.addEventListener("change", () => { if (S.prefs.theme === "auto") applyPrefs(); });
function setPref(k, v) { S.prefs[k] = v; store.set(KEY() + ".prefs", S.prefs); applyPrefs(); if (k === "widgets") widgets(); }
function nativeWindow(id, name, icon, el, size) {
  const open = [...S.wins.values()].find((w) => w.app?.id === id); if (open) { restore(open); return open; }
  return createWindow({ app: { id, name, icon }, native: el, rect: { ...defaultRect(size) } });
}
function openSettings() {
  closeMenus();
  const div = document.createElement("div"); div.className = "native settings";
  const choice = (k, opts) => `<div class="choice" data-pref="${k}">${opts.map(([v, l]) => `<button data-v="${v}" class="${S.prefs[k] === v ? "on" : ""}">${l}</button>`).join("")}</div>`;
  div.innerHTML = `<h2>Settings</h2><p class="muted">These are saved in this browser, for you.</p>
    <section><h3>Look</h3>
      <p class="small muted">Theme</p>${choice("theme", [["auto", "Match my device"], ["dark", "Dark"], ["light", "Light"]])}
      <p class="small muted" style="margin-top:10px">Wallpaper</p>${choice("wallpaper", [["hearth", "Hearth"], ["ember", "Ember"], ["plain", "Plain"]])}</section>
    <section><h3>Size</h3>
      <p class="small muted">Text</p>${choice("text", [["s", "Small"], ["m", "Normal"], ["l", "Large"], ["xl", "Larger"]])}
      <p class="small muted" style="margin-top:10px">Icons</p>${choice("icons", [["s", "Small"], ["m", "Medium"], ["l", "Large"]])}</section>
    <section><h3>Desktop</h3>
      <label><input type="checkbox" data-bool="widgets" ${S.prefs.widgets ? "checked" : ""}> Show today's widgets (meals, shopping, budget)</label>
      <label><input type="checkbox" data-bool="restore" ${S.prefs.restore ? "checked" : ""}> Reopen my windows next time</label></section>
    <section><h3>Windows</h3><div class="row"><button data-do="tidy">Tidy windows</button><button data-do="closeall">Close all windows</button><button data-do="install" ${deferredInstall ? "" : "hidden"}>Install as an app</button><button data-do="unpin" ${S.prefs.pinned.length ? "" : "hidden"}>Clear pinned apps</button></div></section>
    <section><h3>Signed in as</h3><dl><dt>Name</dt><dd>${esc(S.me.name)}</dd><dt>User</dt><dd>${esc(S.me.username)}</dd><dt>Email</dt><dd>${esc(S.me.email)}</dd><dt>Apps</dt><dd>${S.apps.length}</dd><dt>Desktop</dt><dd>version ${esc(S.version)}</dd></dl></section>
    <div class="row"><button data-do="welcome">Show the welcome tips again</button><button data-do="help">Open Help</button></div>`;
  div.addEventListener("click", (e) => {
    const b = e.target.closest(".choice button");
    if (b) { const k = b.parentElement.dataset.pref; setPref(k, b.dataset.v); $$("button", b.parentElement).forEach((x) => x.classList.toggle("on", x === b)); }
    const d = e.target.closest("[data-do]")?.dataset.do;
    if (d === "tidy") tidy();
    if (d === "closeall") [...S.wins.values()].filter((w) => !w.native).forEach(closeWin);
    if (d === "install") promptInstall();
    if (d === "unpin") { S.prefs.pinned = []; store.set(KEY() + ".prefs", S.prefs); renderPins(); e.target.hidden = true; }
    if (d === "welcome") welcome();
    if (d === "help") openHelp();
  });
  div.addEventListener("change", (e) => { const k = e.target.dataset.bool; if (k) setPref(k, e.target.checked); });
  nativeWindow("settings", "Settings", "/icons/settings.svg", div, { w: 600, h: 720 });
}
function supportLine() {
  const n = S.support.name || "the person who runs HeardOS", e = S.support.email;
  return e ? `ask <a href="mailto:${esc(e)}">${esc(n)}</a>` : `ask ${esc(n)}`;
}
function detailsText() {
  return [`HeardOS desktop ${S.version}`, `When: ${new Date().toString()}`, `Who: ${S.me?.username || "?"}`, `Page: ${location.href}`,
    `Browser: ${navigator.userAgent}`, `Screen: ${innerWidth}×${innerHeight}${isPhone() ? " (phone layout)" : ""}, online: ${navigator.onLine}`,
    `Open windows: ${[...S.wins.values()].map((w) => w.app.id + (w.min ? " (minimised)" : "")).join(", ") || "none"}`,
    `Apps available: ${S.apps.map((a) => a.id).join(", ")}`].join("\n");
}
function openHelp() {
  closeMenus();
  const div = document.createElement("div"); div.className = "native help";
  div.innerHTML = `<h2>Help</h2><p class="muted">HeardOS is the family's own apps, all in one place and already signed in.</p>
    <section><h3>Getting around</h3><ul>
      <li><b>Open an app:</b> double-click its icon (one tap on a phone), or press <kbd>Start</kbd> and start typing its name.</li>
      <li><b>Windows:</b> drag the title bar to move; drag an edge to resize; drag to the left or right edge of the screen to fill half, or to the top to fill the screen. Double-click the title bar to maximise.</li>
      <li><b>Taskbar:</b> every open window has a button at the bottom. Click it to bring the window up or put it away. Middle-click closes.</li>
      <li><b>Pin favourites:</b> right-click (or press and hold) an icon and choose <i>Pin to taskbar</i>.</li>
      <li><b>↗ in a title bar</b> opens that app in a normal browser tab, which helps if it misbehaves inside a window.</li>
      <li><b>On a phone</b> every app fills the screen; the back button closes it.</li>
      <li><b>Install it:</b> the <i>Install</i> button (when your browser offers it) puts HeardOS in your app list like any other app.</li></ul></section>
    <section><h3>Keyboard</h3><table>
      <tr><td><kbd>Ctrl</kbd>+<kbd>Space</kbd> or <kbd>/</kbd></td><td>Start menu and search</td></tr>
      <tr><td><kbd>F1</kbd></td><td>This help</td></tr>
      <tr><td><kbd>Ctrl</kbd>+<kbd>\`</kbd></td><td>Switch to the next window (<kbd>Shift</kbd> for the previous)</td></tr>
      <tr><td><kbd>Tab</kbd> to a title bar, then arrows</td><td>Move the window (<kbd>Shift</kbd>+arrows resize, <kbd>Ctrl</kbd> for fine steps)</td></tr>
      <tr><td>Title bar: <kbd>Enter</kbd> · <kbd>M</kbd> · <kbd>Delete</kbd></td><td>Maximise · minimise · close</td></tr>
      <tr><td><kbd>Esc</kbd></td><td>Close any menu</td></tr></table></section>
    <section><h3>If something's wrong</h3><ul>
      <li><b>An app shows a sign-in page or won't load:</b> press ⟳ on its window. If that doesn't help, use ↗ to open it in a tab.</li>
      <li><b>"You've been signed out":</b> sign-ins last a while and then expire; sign in again and your windows come back.</li>
      <li><b>"Can't reach home":</b> the home server or your internet is down. The desktop keeps checking by itself.</li>
      <li><b>An app you expect is missing:</b> you haven't been given access to it yet; ${supportLine()}.</li>
      <li><b>Still stuck?</b> Copy the details below and ${supportLine()}.</li></ul>
      <div class="row"><button data-do="copy">Copy details for support</button>${S.support.email ? `<button data-do="email">Email ${esc(S.support.name || "support")}</button>` : ""}${S.admin ? `<button data-do="system">Open System</button>` : ""}</div>
      <pre class="details" hidden></pre></section>
    <p class="muted small">HeardOS desktop ${esc(S.version)}</p>`;
  div.addEventListener("click", async (e) => {
    const d = e.target.closest("[data-do]")?.dataset.do;
    if (d === "copy") { const t = detailsText(); try { await navigator.clipboard.writeText(t); toast("Details copied. Paste them into a message."); } catch { const p = $("pre.details", div); p.textContent = t; p.hidden = false; } }
    if (d === "email") location.href = `mailto:${S.support.email}?subject=${encodeURIComponent("HeardOS desktop problem")}&body=${encodeURIComponent(detailsText())}`;
    if (d === "system") openSystem();
  });
  nativeWindow("help", "Help", "/icons/help.svg", div, { w: 720, h: 760 });
}
function welcome() {
  const old = $(".welcome-wrap"); if (old) old.remove();
  const wrap = document.createElement("div"); wrap.className = "welcome-wrap"; wrap.setAttribute("role", "dialog"); wrap.setAttribute("aria-label", "Welcome");
  wrap.innerHTML = `<div class="welcome"><h2>Welcome, ${esc(firstName())}</h2><p class="muted">This is the family's desktop: all our apps, already signed in. Three things to know:</p><ol>
    <li><b>Double-click an icon</b> (one tap on a phone) to open an app. It opens in a window you can move and resize.</li>
    <li><b>Start</b> (bottom left) finds any app: just start typing its name.</li>
    <li>Stuck? Press <b>?</b> at the bottom right or <kbd>F1</kbd> for help.</li></ol>
    <div class="row"><button data-do="more">Show me Help</button><button class="primary" data-do="ok">Got it</button></div></div>`;
  wrap.addEventListener("click", (e) => { const d = e.target.closest("[data-do]")?.dataset.do; if (!d) return; setPref("welcomed", true); wrap.remove(); if (d === "more") openHelp(); });
  document.body.append(wrap); $("button.primary", wrap).focus();
}
async function openSystem() {
  closeMenus();
  const div = document.createElement("div"); div.className = "native system"; div.innerHTML = `<h2>System</h2><p class="muted">Loading…</p>`;
  const w = nativeWindow("system", "System", "/icons/system.svg", div, { w: 760, h: 780 });
  const render = async () => {
    let d;
    try { d = await api("/api/diagnostics"); } catch (e) { div.innerHTML = `<h2>System</h2><p>Couldn't load diagnostics (${esc(e.message)}).</p>`; return; }
    const ago = (iso) => iso ? `${Math.round((Date.now() - new Date(iso)) / 60000)} min ago` : "never";
    const up = d.uptimeSeconds, upTxt = up > 86400 ? `${Math.floor(up / 86400)} d ${Math.floor(up % 86400 / 3600)} h` : up > 3600 ? `${Math.floor(up / 3600)} h ${Math.floor(up % 3600 / 60)} min` : `${Math.floor(up / 60)} min`;
    const src = Object.entries(d.sources).map(([k, v]) => `<tr><td>${esc(k)}</td><td><span class="pill ${v.state === "ok" ? "ok" : v.state === "error" ? "bad" : "off"}">${esc(v.state || "not run yet")}</span></td><td>${esc(ago(v.lastOk))}</td><td>${v.ms != null ? v.ms + " ms" : ""}</td><td class="muted small">${esc(v.lastError ? `${v.lastError} (${ago(v.lastErrorAt)})` : "")}</td></tr>`).join("");
    div.innerHTML = `<h2>System</h2><p class="muted">Desktop ${esc(d.version)} on Node ${esc(d.node)} · up ${esc(upTxt)} · container ${esc(d.hostname)} · ${esc(String(d.apps))} apps in the catalogue</p>
      <section><h3>Checklist</h3>${d.checks.map((c) => `<div class="check ${c.ok ? "ok" : "bad"}"><span class="mark">${c.ok ? "✓" : "!"}</span><div>${esc(c.label)}${c.hint ? `<div class="hint">${esc(c.hint)}</div>` : ""}</div></div>`).join("")}</section>
      <section><h3>Data sources (This week + widgets)</h3><table><tr><th>Source</th><th>State</th><th>Last success</th><th>Took</th><th>Last error</th></tr>${src}</table>
        <p class="muted small" style="margin-top:8px">Cached for ${esc(String(d.cacheSeconds))} s; this copy is ${d.cacheAgeSeconds == null ? "not loaded yet" : esc(String(d.cacheAgeSeconds)) + " s old"}.</p>
        <div class="row"><button data-do="refresh">Fetch fresh data now</button><button data-do="reload">Re-check</button></div></section>
      ${d.catalogWarnings.length ? `<section><h3>Catalogue warnings (apps.json)</h3><ul>${d.catalogWarnings.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></section>` : ""}
      <section><h3>Recent errors</h3>${d.recentErrors.length ? `<table><tr><th>When</th><th>Where</th><th>What</th></tr>${d.recentErrors.map((x) => `<tr><td class="muted small">${esc(new Date(x.at).toLocaleString())}</td><td>${esc(x.where)}</td><td class="small">${esc(x.message)}</td></tr>`).join("")}</table>` : `<p class="muted">None since the desktop started.</p>`}</section>
      <section><h3>Where things are</h3><dl><dt>Catalogue file</dt><dd>${esc(d.appsFile)}</dd><dt>Admin group</dt><dd>${esc(d.adminGroup)}</dd><dt>Domain</dt><dd>${esc(d.domain)}</dd><dt>Logs</dt><dd>docker logs dash (one line per event, timestamped)</dd><dt>Guide</dt><dd>apps/dash/README.md in the repo</dd></dl></section>`;
  };
  div.addEventListener("click", async (e) => {
    const d = e.target.closest("[data-do]")?.dataset.do; if (!d) return;
    if (d === "refresh") { e.target.disabled = true; e.target.textContent = "Fetching…"; try { await api("/api/summary?refresh=1"); widgets(); toast("Fresh data fetched."); } catch { toast("Couldn't fetch fresh data; see the sources table.", { bad: true }); } }
    await render();
  });
  await render();
  return w;
}

// ---------------------------------------------------------------- session restore (per person, per browser)
let saveT;
function save() {
  clearTimeout(saveT);
  saveT = setTimeout(() => {
    const order = [...S.wins.values()].sort((a, b) => a.el.style.zIndex - b.el.style.zIndex);
    store.set(KEY() + ".session", order.filter((w) => !w.native).map((w) => ({ id: w.app.id, rect: w.max ? w.prev || w.rect : w.rect, max: w.max, min: w.min })));
  }, 300);
}
function restoreSession() {
  for (const s of store.get(KEY() + ".session", [])) {
    const app = appById(s.id); if (!app || !app.embed || app.native) continue;
    const D = $("#desktop").getBoundingClientRect();
    const r = s.rect && s.rect.x < D.width - 80 && s.rect.y < D.height - 40 ? s.rect : undefined;   // screen may be smaller now
    createWindow({ app, url: app.id === "week" ? `${app.url}?theme=${resolvedTheme()}` : app.url, rect: r, max: s.max, min: s.min });
  }
}
addEventListener("resize", () => { const D = $("#desktop").getBoundingClientRect(); S.wins.forEach((w) => { if (w.max) { w.rect = { x: 0, y: 0, w: D.width, h: D.height }; place(w); } }); });

// ---------------------------------------------------------------- phones: the back button closes the window on top
function pushPhoneHistory(w) { history.pushState({ heardosWin: w.id }, ""); }
addEventListener("popstate", () => {
  if (S.ignorePop) { S.ignorePop = false; return; }
  const top = topVisible();
  if (top && isPhone()) closeWin(top, true);
});

// ---------------------------------------------------------------- install as an app (PWA)
let deferredInstall = null;
addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferredInstall = e; $("#install").hidden = false; });
async function promptInstall() { if (!deferredInstall) return; deferredInstall.prompt(); await deferredInstall.userChoice; deferredInstall = null; $("#install").hidden = true; }
$("#install").addEventListener("click", promptInstall);
addEventListener("appinstalled", () => toast("HeardOS is installed. Find it in your app launcher."));
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

boot();
