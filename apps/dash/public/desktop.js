// HeardOS desktop (2026-10-18). Each app opens in a window as an <iframe> of its own address. All apps share the
// family domain, so the browser sends the Authentik session inside the frame: people are already signed in.
// The lighthouse Caddy lets only this page (and each app itself) frame the apps; see docs/web-desktop.md.
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {   // localStorage can throw (private mode, blocked storage): the desktop must work without it
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
const IFRAME_ALLOW = "fullscreen; clipboard-read; clipboard-write; publickey-credentials-get; publickey-credentials-create; camera; microphone; geolocation; autoplay; picture-in-picture; display-capture; web-share";
const isPhone = () => matchMedia("(max-width: 760px)").matches;

const S = { me: null, apps: [], folders: [], links: {}, wins: new Map(), z: 10, active: null, seq: 0, prefs: {} };
const KEY = () => `heardos.desktop.${S.me?.username || "anon"}`;

// ---------------------------------------------------------------- boot
async function boot() {
  try {
    const r = await fetch("/api/apps", { cache: "no-store" });
    if (!r.ok) throw new Error(`apps: HTTP ${r.status}`);
    const d = await r.json();
    S.apps = d.apps; S.folders = d.folders; S.links = d.links;
    S.me = await (await fetch("/api/me", { cache: "no-store" })).json();
  } catch (e) {
    toast(`Couldn't load your apps (${esc(e.message)}). <a href="">Reload</a>`); return;
  }
  S.prefs = { wallpaper: "hearth", icons: "m", widgets: true, restore: true, ...store.get(KEY() + ".prefs", {}) };
  applyPrefs();
  renderUser(); renderIcons(); renderStartList(""); clock(); setInterval(clock, 15000);
  widgets(); setInterval(widgets, 5 * 60 * 1000);
  if (S.prefs.restore) restoreSession();
  const want = new URLSearchParams(location.search).get("open");   // /?open=<app id> (launcher shortcuts)
  if (want && S.apps.some((a) => a.id === want)) openApp(want);
}

// ---------------------------------------------------------------- user, clock, toasts
const initials = (n) => (n || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
function renderUser() {
  const n = S.me.name || S.me.username || "Signed in";
  $("#user").textContent = initials(n); $("#user").title = `${n} — account`;
  $("#sm-avatar").textContent = initials(n); $("#sm-name").textContent = n; $("#sm-email").textContent = S.me.email || "";
}
function clock() {
  const d = new Date();
  $("#clock").innerHTML = `${d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}<br><span class="muted">${d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}</span>`;
}
function toast(html, ms = 6000) {
  const t = document.createElement("div"); t.className = "toast"; t.innerHTML = html;
  $("#toasts").append(t); setTimeout(() => t.remove(), ms);
}

// ---------------------------------------------------------------- desktop icons
function renderIcons() {
  const box = $("#icons"); box.innerHTML = "";
  for (const f of S.folders) for (const a of S.apps.filter((x) => x.folder === f)) {
    const b = document.createElement("button");
    b.className = "dicon"; b.dataset.app = a.id; b.title = a.description || a.name; b.setAttribute("role", "listitem");
    b.innerHTML = `<img src="${esc(a.icon)}" alt=""><span>${esc(a.name)}</span>`;
    b.addEventListener("click", (e) => {   // mouse: select, double-click opens · touch/pen: one tap opens
      $$(".dicon.sel").forEach((x) => x.classList.remove("sel")); b.classList.add("sel");
      const touch = e.pointerType ? e.pointerType !== "mouse" : matchMedia("(pointer: coarse)").matches;
      if (touch && e.detail) openApp(a.id);   // e.detail = 0 for keyboard "clicks" (Enter is handled below)
    });
    b.addEventListener("dblclick", () => openApp(a.id));
    b.addEventListener("keydown", (e) => { if (e.key === "Enter") openApp(a.id); });
    box.append(b);
  }
}
$("#desktop").addEventListener("pointerdown", (e) => { if (e.target.id === "desktop" || e.target.id === "icons") $$(".dicon.sel").forEach((x) => x.classList.remove("sel")); });

// ---------------------------------------------------------------- windows
function appById(id) { return S.apps.find((a) => a.id === id); }
function appForUrl(u) {   // the app whose address this URL is on (longest prefix wins)
  let best = null;
  for (const a of S.apps) { const base = new URL(a.url, location.href); const t = new URL(u, location.href);
    if (base.origin === t.origin && (!best || base.pathname.length > new URL(best.url, location.href).pathname.length)) best = a; }
  return best;
}
function openApp(id, url) {
  if (id === "settings") return openSettings();
  const app = appById(id); if (!app) return;
  closeMenus();
  if (!app.embed) { window.open(url || app.url, "_blank", "noopener"); return; }
  const open = [...S.wins.values()].find((w) => w.app?.id === id);
  if (open) { if (url) open.iframe.src = url; restore(open); focus(open); return open; }
  return createWindow({ app, url: url || app.url });
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
  const body = $(".wbody", el);
  if (native) {
    body.classList.add("native"); $(".loading", el).classList.add("done");
    $$('[data-act="reload"],[data-act="popout"]', el).forEach((b) => b.remove());
    body.append(native);
  } else {
    const f = document.createElement("iframe");
    f.title = app.name; f.allow = IFRAME_ALLOW; f.referrerPolicy = "strict-origin-when-cross-origin";
    f.src = url; w.iframe = f; body.append(f);
    const ld = $(".loading", el), slow = $(".slow", el);
    $("a", slow).href = url;
    const tmr = setTimeout(() => (slow.hidden = false), 15000);
    f.addEventListener("load", () => { clearTimeout(tmr); ld.classList.add("done"); });
  }
  wireWindow(w);
  S.wins.set(id, w);
  $("#desktop").append(el);
  place(w);
  if (max) toggleMax(w, true);
  el.classList.add("opening"); setTimeout(() => el.classList.remove("opening"), 200);
  addTask(w);
  if (min) minimize(w); else focus(w);
  save();
  return w;
}
function place(w) { const r = w.rect, s = w.el.style; s.left = r.x + "px"; s.top = r.y + "px"; s.width = r.w + "px"; s.height = r.h + "px"; }
function focus(w) {
  if (!w || w.min) return;
  S.active = w.id; w.el.style.zIndex = ++S.z;
  $$(".win").forEach((x) => x.classList.toggle("active", x === w.el));
  $$(".task").forEach((t) => t.classList.toggle("active", t.dataset.win === w.id));
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
function closeWin(w) {
  w.el.remove(); $(`.task[data-win="${w.id}"]`)?.remove(); S.wins.delete(w.id);
  if (S.active === w.id) { S.active = null; focus(topVisible()); }
  save();
}
function wireWindow(w) {
  const el = w.el;
  el.addEventListener("pointerdown", () => focus(w), true);
  $(".tbtns", el).addEventListener("click", (e) => {
    const act = e.target.closest("button")?.dataset.act; if (!act) return;
    if (act === "close") closeWin(w);
    else if (act === "min") minimize(w);
    else if (act === "max") toggleMax(w);
    else if (act === "reload") { $(".loading", el).classList.remove("done"); w.iframe.src = w.iframe.src; }
    else if (act === "popout") { window.open(w.iframe.src, "_blank", "noopener"); }
  });
  const tb = $(".titlebar", el);
  tb.addEventListener("dblclick", (e) => { if (!e.target.closest("button") && !isPhone()) toggleMax(w); });
  tb.addEventListener("pointerdown", (e) => { if (!e.target.closest("button") && e.button === 0 && !isPhone()) drag(w, e); });
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

// ---------------------------------------------------------------- taskbar
function addTask(w) {
  const b = document.createElement("button");
  b.className = "task"; b.dataset.win = w.id; b.setAttribute("role", "tab"); b.title = w.app.name;
  b.innerHTML = `<img src="${esc(w.app.icon)}" alt=""><span>${esc(w.app.name)}</span>`;
  b.addEventListener("click", () => { if (w.min) restore(w); else if (S.active === w.id) minimize(w); else focus(w); });
  b.addEventListener("auxclick", (e) => { if (e.button === 1) closeWin(w); });   // middle-click closes, like a browser tab
  $("#tasks").append(b);
}

// ---------------------------------------------------------------- start menu + user menu
function closeMenus() { $("#start-menu").hidden = true; $("#user-menu").hidden = true; $("#start").classList.remove("open"); }
function toggleStart(force) {
  const m = $("#start-menu"), show = force ?? m.hidden;
  closeMenus();
  if (show) { m.hidden = false; $("#start").classList.add("open"); $("#sm-search").value = ""; renderStartList(""); setTimeout(() => $("#sm-search").focus(), 0); }
}
function renderStartList(q) {
  q = q.trim().toLowerCase();
  const hit = (a) => !q || `${a.name} ${a.description} ${a.folder}`.toLowerCase().includes(q);
  let html = "";
  for (const f of S.folders) {
    const list = S.apps.filter((a) => a.folder === f && hit(a)); if (!list.length) continue;
    html += `<div class="sm-folder">${esc(f)}</div>` + list.map((a) => `<button class="sm-app" data-app="${esc(a.id)}"><img src="${esc(a.icon)}" alt=""><div><div>${esc(a.name)}</div><div class="d">${esc(a.description || "")}</div></div>${a.embed ? "" : '<span class="ext">↗</span>'}</button>`).join("");
  }
  $("#sm-list").innerHTML = html || `<p class="muted" style="padding:8px">No app matches “${esc(q)}”.</p>`;
  $(".sm-app", $("#sm-list"))?.classList.add("hl");
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
$("#user").addEventListener("click", () => { const m = $("#user-menu"), show = m.hidden; closeMenus(); m.hidden = !show; });
document.addEventListener("click", (e) => {
  const act = e.target.closest("[data-action]")?.dataset.action; if (!act) return;
  closeMenus();
  if (act === "settings") openSettings();
  else if (act === "account") { if (appById("account")) openApp("account"); else window.open(S.links.account, "_blank", "noopener"); }
  else if (act === "arrange") tidy();
  else if (act === "minimize-all") S.wins.forEach((w) => !w.min && minimize(w));
  else if (act === "signout") { save(); location.href = S.links.signOut; }
});
document.addEventListener("pointerdown", (e) => {
  if (!e.target.closest("#start-menu, #start, #user-menu, #user")) closeMenus();
});
addEventListener("blur", () => setTimeout(closeMenus, 0));   // clicking into an app window (iframe) closes menus
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey && e.code === "Space") || (e.key === "/" && e.target === document.body)) { e.preventDefault(); toggleStart(); }
  else if (e.key === "Escape") closeMenus();
});
// week.html (and any same-origin page in a window) can ask to open another app
addEventListener("message", (e) => {
  if (e.origin !== location.origin || e.data?.type !== "heardos:open-url") return;
  const a = appForUrl(e.data.url);
  if (a) openApp(a.id, e.data.url); else window.open(e.data.url, "_blank", "noopener");
});

// ---------------------------------------------------------------- widgets (from /api/summary, same data as This week)
const money = (n) => (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
async function widgets() {
  const box = $("#widgets");
  if (!S.prefs.widgets || !appById("week")) { document.body.classList.add("no-widgets"); return; }
  document.body.classList.remove("no-widgets");
  const d = new Date();
  let html = `<div class="widget" data-open="week"><h3>${esc(d.toLocaleDateString(undefined, { weekday: "long" }))}</h3><div class="big">${esc(d.toLocaleDateString(undefined, { month: "long", day: "numeric" }))}</div><div class="muted small">Hello, ${esc((S.me.name || "").split(" ")[0] || "there")}</div></div>`;
  try {
    const s = await (await fetch("/api/summary", { cache: "no-store" })).json();
    const today = d.toISOString().slice(0, 10), m = s.mealie || {}, a = s.actual || {}, h = s.homebox || {};
    if (!m.error) {
      const meals = (m.meals || []).filter((x) => x.date >= today).slice(0, 4);
      html += `<div class="widget" data-open="recipes"><h3>Meals</h3>${meals.length ? `<ul>${meals.map((x) => `<li><b>${x.date === today ? "Today" : esc(new Date(x.date + "T12:00").toLocaleDateString(undefined, { weekday: "short" }))}</b>${esc(x.title)}</li>`).join("")}</ul>` : '<span class="muted">Nothing planned</span>'}</div>`;
      const open = (m.shopping || []).reduce((n, l) => n + l.open.length, 0);
      html += `<div class="widget" data-open="recipes"><h3>Shopping</h3><div class="big">${open}</div><div class="muted small">item${open === 1 ? "" : "s"} to buy</div></div>`;
    }
    if (!a.error && a.month) html += `<div class="widget" data-open="budget"><h3>Budget · ${esc(a.month)}</h3><ul><li><b>Spent</b>${money(Math.abs(a.totalSpent))}</li><li><b>Left</b><span class="${a.toBudget < 0 ? "neg" : "pos"}">${money(a.toBudget)}</span> to budget</li></ul></div>`;
    if (!h.error && h.totals) html += `<div class="widget" data-open="inventory"><h3>At home</h3><ul><li><b>${esc(h.totals.items)}</b>things in ${esc(h.totals.locations)} places</li></ul></div>`;
  } catch {}
  box.innerHTML = html;
}
$("#widgets").addEventListener("click", (e) => { const w = e.target.closest("[data-open]"); if (w) openApp(appById(w.dataset.open) ? w.dataset.open : "week"); });

// ---------------------------------------------------------------- settings (a native window, not an iframe)
function applyPrefs() {
  document.body.dataset.wallpaper = S.prefs.wallpaper;
  document.body.classList.remove("icons-s", "icons-m", "icons-l"); document.body.classList.add(`icons-${S.prefs.icons}`);
}
function setPref(k, v) { S.prefs[k] = v; store.set(KEY() + ".prefs", S.prefs); applyPrefs(); if (k === "widgets") widgets(); }
function openSettings() {
  closeMenus();
  const open = [...S.wins.values()].find((w) => w.app?.id === "settings"); if (open) { restore(open); return; }
  const div = document.createElement("div"); div.className = "settings";
  const choice = (k, opts) => `<div class="choice" data-pref="${k}">${opts.map(([v, l]) => `<button data-v="${v}" class="${S.prefs[k] === v ? "on" : ""}">${l}</button>`).join("")}</div>`;
  div.innerHTML = `<h2>Settings</h2><p class="muted">Saved in this browser.</p>
    <section><h3>Wallpaper</h3>${choice("wallpaper", [["hearth", "Hearth"], ["ember", "Ember"], ["plain", "Plain"]])}</section>
    <section><h3>Icon size</h3>${choice("icons", [["s", "Small"], ["m", "Medium"], ["l", "Large"]])}</section>
    <section><h3>Desktop</h3>
      <label><input type="checkbox" data-bool="widgets" ${S.prefs.widgets ? "checked" : ""}> Show today's widgets (meals, shopping, budget)</label>
      <label><input type="checkbox" data-bool="restore" ${S.prefs.restore ? "checked" : ""}> Reopen my windows next time</label></section>
    <section><h3>Windows</h3><div class="row"><button data-do="tidy">Tidy windows</button><button data-do="closeall">Close all windows</button><button data-do="install" ${deferredInstall ? "" : "hidden"}>Install as an app</button></div></section>
    <section><h3>Signed in as</h3><dl><dt>Name</dt><dd>${esc(S.me.name)}</dd><dt>User</dt><dd>${esc(S.me.username)}</dd><dt>Email</dt><dd>${esc(S.me.email)}</dd><dt>Apps</dt><dd>${S.apps.length}</dd></dl></section>
    <p class="muted small">Tip: Ctrl+Space opens Start and search. Drag a window to the left or right edge to fill half the screen, or to the top to maximise.</p>`;
  div.addEventListener("click", (e) => {
    const b = e.target.closest(".choice button");
    if (b) { const k = b.parentElement.dataset.pref; setPref(k, b.dataset.v); $$("button", b.parentElement).forEach((x) => x.classList.toggle("on", x === b)); }
    const d = e.target.closest("[data-do]")?.dataset.do;
    if (d === "tidy") tidy();
    if (d === "closeall") [...S.wins.values()].filter((w) => w.app.id !== "settings").forEach(closeWin);
    if (d === "install") promptInstall();
  });
  div.addEventListener("change", (e) => { const k = e.target.dataset.bool; if (k) setPref(k, e.target.checked); });
  createWindow({ app: { id: "settings", name: "Settings", icon: "/icons/settings.svg" }, native: div, rect: { ...defaultRect({ w: 560, h: 620 }) } });
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
    const app = appById(s.id); if (!app || !app.embed) continue;
    const D = $("#desktop").getBoundingClientRect();
    const r = s.rect && s.rect.x < D.width - 80 && s.rect.y < D.height - 40 ? s.rect : undefined;   // screen may be smaller now
    createWindow({ app, url: app.url, rect: r, max: s.max, min: s.min });
  }
}
addEventListener("resize", () => { const D = $("#desktop").getBoundingClientRect(); S.wins.forEach((w) => { if (w.max) { w.rect = { x: 0, y: 0, w: D.width, h: D.height }; place(w); } }); });

// ---------------------------------------------------------------- install as an app (PWA)
let deferredInstall = null;
addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferredInstall = e; $("#install").hidden = false; });
async function promptInstall() { if (!deferredInstall) return; deferredInstall.prompt(); await deferredInstall.userChoice; deferredInstall = null; $("#install").hidden = true; }
$("#install").addEventListener("click", promptInstall);
addEventListener("appinstalled", () => toast("HeardOS is installed. Find it in your app launcher."));
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

boot();
