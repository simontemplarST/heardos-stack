// HeardOS desktop backend (summary page 2026-10-13; desktop shell 2026-10-18; serviceability pass 2026-10-19).
// The page is a web desktop (public/index.html + desktop.js): apps open signed in, in windows, through the Authentik
// gate in front of this container. /api/me and /api/apps come from the X-authentik-* headers that gate adds;
// /api/apps filters apps.json by the person's app-<slug> groups. The old summary page lives on as public/week.html.
// Pulls: Mealie (meal plan + shopping lists), Actual Budget (month summary, accounts, upcoming schedules),
// Homebox (inventory totals, locations, recent items). Each source is optional: missing env => section
// reports "not configured"; a failing source reports "unavailable" (details only in the container log and in
// /api/diagnostics for admins) instead of taking the page down. Sections are filtered per person: Actual needs
// app-actual-budget, Mealie app-mealie, Homebox app-homebox; otherwise the section is {error: "no access"}.
// Gate: every path but /healthz needs X-Dash-Gate == DASH_GATE_SECRET (Traefik adds it on the dash router, after the
// Authentik forward auth), so other containers on the proxy network can't call the desktop directly with forged
// X-authentik-* headers. /api/* also needs a non-empty X-authentik-username.
// Operator view: /api/diagnostics (members of DASH_ADMIN_GROUP only) = version, uptime, config checklist, each source's
// last success / last error, apps.json validation warnings and the last errors. The System app in the desktop shows it.
// Settings (env): see README.md — every variable is documented there.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import crypto from "node:crypto";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 8080);
const HOST = process.env.LISTEN_HOST || "0.0.0.0";   // inside the container; Traefik reaches it over the proxy network
const CACHE_MS = Number(process.env.CACHE_SECONDS || 180) * 1000;
const env = (k) => (process.env[k] || "").trim();
const VERSION = (() => { try { return JSON.parse(fs.readFileSync(path.join(__dirname, "package.json"), "utf8")).version; } catch { return "?"; } })();
const STARTED = Date.now();

// ---------- logging: one line per event, timestamped, levelled (DASH_LOG_LEVEL=debug|info|warn|error) ----------
const LEVELS = { debug: 0, info: 1, warn: 2, error: 3 };
const LOG_LEVEL = LEVELS[env("DASH_LOG_LEVEL")] ?? LEVELS.info;
const log = Object.fromEntries(Object.keys(LEVELS).map((l) => [l, (msg, extra) => {
  if (LEVELS[l] < LOG_LEVEL) return;
  const line = `${new Date().toISOString()} ${l.toUpperCase().padEnd(5)} ${msg}${extra ? " " + JSON.stringify(extra) : ""}`;
  (l === "error" || l === "warn" ? console.error : console.log)(line);
}]));

// ---------- source health: what the System app and /api/diagnostics show ----------
const health = { mealie: {}, actual: {}, homebox: {} };   // {state, lastOk, lastError, lastErrorAt, ms}
const recentErrors = [];                                  // last 30 {at, where, message}
function noteError(where, e) {
  const message = String(e?.message || e);
  recentErrors.unshift({ at: new Date().toISOString(), where, message });
  recentErrors.length = Math.min(recentErrors.length, 30);
}

// ---------- helpers ----------
async function getJSON(url, headers = {}, timeoutMs = 15000) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const r = await fetch(url, { headers, signal: ac.signal });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText} for ${url.replace(/\/\/.*@/, "//")}`);   // logged, never sent to the browser
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}
const isoDate = (d) => d.toISOString().slice(0, 10);
const addDays = (d, n) => new Date(d.getTime() + n * 86400000);
const cents = (n) => Math.round(n || 0) / 100;

// ---------- Mealie ----------
async function mealie() {
  const base = env("MEALIE_URL").replace(/\/$/, "");
  const token = env("MEALIE_TOKEN");
  if (!base || !token) return { error: "not configured" };
  const h = { Authorization: `Bearer ${token}` };
  const today = new Date();
  const plan = await getJSON(`${base}/api/households/mealplans?start_date=${isoDate(today)}&end_date=${isoDate(addDays(today, 6))}&perPage=-1`, h);
  const meals = (plan.items || [])
    .map((e) => ({ date: e.date, type: e.entryType, title: e.recipe?.name || e.title || "", slug: e.recipe?.slug || null }))
    .sort((a, b) => (a.date + a.type).localeCompare(b.date + b.type));
  const lists = await getJSON(`${base}/api/households/shopping/lists?perPage=20`, h);
  const shopping = [];
  for (const l of lists.items || []) {
    const items = await getJSON(`${base}/api/households/shopping/items?queryFilter=${encodeURIComponent(`shoppingListId = ${l.id}`)}&perPage=200`, h);
    const open = (items.items || []).filter((i) => !i.checked).map((i) => ({ text: i.display || i.note || "", label: i.label?.name || null }));
    shopping.push({ id: l.id, name: l.name, open, checked: (items.items || []).length - open.length });
  }
  shopping.sort((a, b) => b.open.length - a.open.length);
  return { meals, shopping, link: env("MEALIE_PUBLIC_URL") || base };
}

// ---------- Actual Budget ----------
let actualApi = null;
let actualReady = false;
async function actual() {
  const serverURL = env("ACTUAL_URL");
  const password = env("ACTUAL_PASSWORD");
  const syncId = env("ACTUAL_SYNC_ID");
  if (!serverURL || !password || !syncId) return { error: "not configured" };
  if (!actualApi) actualApi = await import("@actual-app/api");
  const api = actualApi;
  const dataDir = env("ACTUAL_DATA_DIR") || "/data/actual";
  fs.mkdirSync(dataDir, { recursive: true });
  if (!actualReady) {
    // the sync server is OpenID-only: ACTUAL_PASSWORD holds a session token, which init() takes as sessionToken
    await api.init({ dataDir, serverURL, sessionToken: password });
    actualReady = true;
  }
  // downloadBudget also applies pending sync messages, so the numbers are current.
  await api.downloadBudget(syncId);
  const month = new Date().toISOString().slice(0, 7);
  const bm = await api.getBudgetMonth(month);
  const categories = [];
  for (const g of bm.categoryGroups || []) {
    if (g.is_income || g.hidden) continue;
    for (const c of g.categories || []) {
      if (c.hidden) continue;
      categories.push({ group: g.name, name: c.name, budgeted: cents(c.budgeted), spent: cents(c.spent), balance: cents(c.balance) });
    }
  }
  // most active first: overspent, then by spend
  categories.sort((a, b) => (a.balance < 0) - (b.balance < 0) ? (a.balance < 0 ? -1 : 1) : Math.abs(b.spent) - Math.abs(a.spent));
  const accounts = [];
  for (const a of (await api.getAccounts()).filter((a) => !a.closed)) {
    accounts.push({ name: a.name, offbudget: !!a.offbudget, balance: cents(await api.getAccountBalance(a.id)) });
  }
  const today = isoDate(new Date());
  const horizon = isoDate(addDays(new Date(), 14));
  const schedules = (await api.getSchedules())
    .filter((s) => !s.completed && s.next_date && s.next_date >= today && s.next_date <= horizon)
    .map((s) => ({ name: s.name, date: s.next_date, amount: cents(typeof s._amount === "number" ? s._amount : s._amount?.num1 ?? 0) }))
    .sort((a, b) => a.date.localeCompare(b.date));
  return {
    month,
    toBudget: cents(bm.toBudget),
    totalBudgeted: cents(bm.totalBudgeted),
    totalSpent: cents(bm.totalSpent),
    totalIncome: cents(bm.totalIncome),
    categories,
    accounts,
    schedules,
    link: env("ACTUAL_PUBLIC_URL") || serverURL,
  };
}

// ---------- Homebox ----------
async function homebox() {
  const base = env("HOMEBOX_URL").replace(/\/$/, "");
  const key = env("HOMEBOX_API_KEY");
  if (!base || !key) return { error: "not configured" };
  const h = { Authorization: `Bearer ${key}` };
  // /groups/statistics/locations returns null on Homebox 0.26, so item counts per location come from the entity tree
  const [stats, tree, recent] = await Promise.all([
    getJSON(`${base}/api/v1/groups/statistics`, h),
    getJSON(`${base}/api/v1/entities/tree`, h),
    getJSON(`${base}/api/v1/entities?pageSize=100&page=1`, h),
  ]);
  // the tree lists locations but not their items, so tally each item's direct parent from the entity list
  const isItem = (i) => !i.archived && !(typeof i.entityType === "object" && i.entityType?.isLocation);
  const tally = {};
  for (const i of (recent.items || []).filter(isItem)) { const k = i.parent?.name || "(no location)"; tally[k] = (tally[k] || 0) + 1; }
  const locations = (Array.isArray(tree) ? tree : []).filter((n) => n.type === "location").map((n) => ({ id: n.id, name: n.name, total: tally[n.name] || 0 }));
  for (const [name, total] of Object.entries(tally)) if (!locations.some((l) => l.name === name)) locations.push({ id: null, name, total });
  const items = (recent.items || [])
    .filter(isItem)
    .sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""))
    .slice(0, 8)
    .map((i) => ({ name: i.name, qty: i.quantity, type: typeof i.entityType === "object" ? i.entityType?.name : i.entityType, parent: i.parent?.name || null, price: i.purchasePrice || 0, created: (i.createdAt || "").slice(0, 10) }));
  return {
    totals: { items: stats.totalItems, locations: stats.totalLocations, tags: stats.totalTags, value: stats.totalItemPrice, withWarranty: stats.totalWithWarranty },
    locations: locations.sort((a, b) => b.total - a.total || a.name.localeCompare(b.name)).slice(0, 8),
    recent: items,
    link: env("HOMEBOX_PUBLIC_URL") || base,
  };
}

// ---------- desktop: who is signed in, which apps they get ----------
const DOMAIN = env("DOMAIN") || "example.com";
const APPS_FILE = env("APPS_FILE") || path.join(__dirname, "apps.json");
const ADMIN_GROUP = env("DASH_ADMIN_GROUP") || "authentik Admins";   // who sees the System app and /api/diagnostics
const SUPPORT = { name: env("SUPPORT_NAME"), email: env("SUPPORT_EMAIL") };   // shown in Help ("ask <name>")
function me(req) {
  const h = (k) => String(req.headers[k] || "");
  const groups = h("x-authentik-groups").split("|").map((g) => g.trim()).filter(Boolean);
  return { username: h("x-authentik-username"), name: h("x-authentik-name") || h("x-authentik-username"), email: h("x-authentik-email"), groups };
}
const isAdmin = (user) => user.groups.includes(ADMIN_GROUP);
const DEFAULT_ALLOW = "fullscreen; clipboard-write; publickey-credentials-get";
const KNOWN_FIELDS = new Set(["id", "name", "description", "folder", "icon", "url", "access", "embed", "allow", "w", "h"]);

// apps.json is read on every request (it is small, and an operator can edit a mounted copy without a rebuild) and
// validated each time: problems become warnings in the log once, and in /api/diagnostics always; a broken entry is
// skipped rather than taking the desktop down.
let lastWarnings = null;
function loadCatalog() {
  const warnings = [];
  let cat;
  try {
    cat = JSON.parse(fs.readFileSync(APPS_FILE, "utf8"));
  } catch (e) {
    warnings.push(`apps.json cannot be read: ${e.message}`);
    cat = { folders: [], apps: [] };
  }
  const folders = Array.isArray(cat.folders) ? cat.folders : [];
  const seen = new Set();
  const apps = [];
  for (const [i, a] of (Array.isArray(cat.apps) ? cat.apps : []).entries()) {
    const where = `apps[${i}]${a?.id ? ` (${a.id})` : ""}`;
    if (!a || typeof a !== "object") { warnings.push(`${where}: not an object`); continue; }
    const missing = ["id", "name", "folder", "icon", "url"].filter((k) => typeof a[k] !== "string" || !a[k]);
    if (missing.length) { warnings.push(`${where}: missing ${missing.join(", ")}; skipped`); continue; }
    if (seen.has(a.id)) { warnings.push(`${where}: duplicate id; skipped`); continue; }
    seen.add(a.id);
    if (!folders.includes(a.folder)) warnings.push(`${where}: folder "${a.folder}" is not in "folders" (it will not show)`);
    const iconFile = a.icon.startsWith("builtin:") ? `icons/${a.icon.slice(8)}.svg` : `icons/${a.icon}.png`;
    if (!fs.existsSync(path.join(__dirname, "public", iconFile))) warnings.push(`${where}: icon file public/${iconFile} is missing (a lettered tile is shown instead)`);
    if (!/^(https?:\/\/|\/)/.test(a.url)) warnings.push(`${where}: url should start with https:// or /`);
    if ("access" in a && a.access !== null && typeof a.access !== "string") warnings.push(`${where}: access must be a slug or null`);
    for (const k of Object.keys(a)) if (!KNOWN_FIELDS.has(k)) warnings.push(`${where}: unknown field "${k}" (ignored)`);
    apps.push({ ...a, iconPath: "/" + iconFile });
  }
  if (JSON.stringify(warnings) !== lastWarnings) { lastWarnings = JSON.stringify(warnings); for (const w of warnings) log.warn(`apps.json: ${w}`); }
  return { folders, apps, warnings };
}
function appsFor(user) {
  const cat = loadCatalog();
  const sub = (s) => (typeof s === "string" ? s.replaceAll("{domain}", DOMAIN) : s);
  const can = (a) => !a.access || user.groups.includes(`app-${a.access}`);
  const apps = cat.apps.filter(can).map((a) => ({
    // iframe permissions per app (apps.json `allow`); most apps get only the safe default
    id: a.id, name: a.name, description: a.description || "", folder: a.folder, icon: a.iconPath, url: sub(a.url), embed: a.embed !== false,
    allow: typeof a.allow === "string" ? a.allow : DEFAULT_ALLOW, w: a.w, h: a.h,
  }));
  return { domain: DOMAIN, version: VERSION, folders: cat.folders, apps, admin: isAdmin(user), support: SUPPORT,
    links: { account: `https://auth.${DOMAIN}/if/user/`, signOut: "/outpost.goauthentik.io/sign_out", brand: `https://cloud.${DOMAIN}/brand` } };
}

// ---------- diagnostics (admins): everything a person needs to see why something is off, without the container log ----------
function diagnostics() {
  const cat = loadCatalog();
  const set = (k) => !!env(k);
  const checks = [   // [label, ok, hint when not ok]
    ["Gate secret set (DASH_GATE_SECRET)", !!GATE, "Set DASH_GATE_SECRET in the stack and the dash-gate Traefik middleware; without it other containers can reach the desktop."],
    ["Domain configured (DOMAIN)", env("DOMAIN") !== "", `DOMAIN is unset: links use "${DOMAIN}".`],
    ["Mealie connected (MEALIE_URL + MEALIE_TOKEN)", set("MEALIE_URL") && set("MEALIE_TOKEN"), "Meals and shopping widgets stay off until both are set."],
    ["Actual Budget connected (ACTUAL_URL + ACTUAL_SYNC_ID + ACTUAL_PASSWORD)", set("ACTUAL_URL") && set("ACTUAL_SYNC_ID") && set("ACTUAL_PASSWORD"), "ACTUAL_PASSWORD is a session token (localStorage user-token in Actual); ACTUAL_SYNC_ID is the budget's group id."],
    ["Homebox connected (HOMEBOX_URL + HOMEBOX_API_KEY)", set("HOMEBOX_URL") && set("HOMEBOX_API_KEY"), "The inventory widget stays off until both are set."],
    ["Support contact set (SUPPORT_EMAIL)", !!SUPPORT.email, "Help shows no one to contact; set SUPPORT_EMAIL (and SUPPORT_NAME)."],
    ["App catalogue valid (apps.json)", cat.warnings.length === 0, "See the warnings below."],
    ["Icons present", cat.apps.every((a) => fs.existsSync(path.join(__dirname, "public", a.iconPath))), "Rebuild the image (fetch-icons.mjs) or add the file under public/icons."],
  ].map(([label, ok, hint]) => ({ label, ok, hint: ok ? "" : hint }));
  return {
    version: VERSION, node: process.version, uptimeSeconds: Math.round((Date.now() - STARTED) / 1000), hostname: os.hostname(),
    domain: DOMAIN, adminGroup: ADMIN_GROUP, appsFile: APPS_FILE, cacheSeconds: CACHE_MS / 1000,
    cacheAgeSeconds: cache.data ? Math.round((Date.now() - cache.at) / 1000) : null, lastSummaryAt: cache.data?.generatedAt || null,
    sources: health, checks, catalogWarnings: cat.warnings, apps: cat.apps.length, recentErrors,
  };
}

// ---------- cache + server ----------
let cache = { at: 0, data: null, inflight: null };
async function section(fn) {
  const h = health[fn.name], t0 = Date.now();
  try {
    const out = await fn();
    Object.assign(h, { state: out.error === "not configured" ? "not configured" : "ok", ms: Date.now() - t0 });
    if (h.state === "ok") h.lastOk = new Date().toISOString();
    return out;
  } catch (e) {
    Object.assign(h, { state: "error", ms: Date.now() - t0, lastError: String(e?.message || e), lastErrorAt: new Date().toISOString() });
    noteError(`summary ${fn.name}`, e);
    log.error(`summary ${fn.name}: ${e?.stack || e}`);   // upstream errors and hostnames stay in the log / diagnostics
    return { error: "unavailable" };
  }
}
const REFRESH_MIN_MS = 60 * 1000;   // ?refresh bypasses the cache at most once a minute, for everyone together
let lastForced = 0;
async function summary(force = false) {
  if (force && Date.now() - lastForced < REFRESH_MIN_MS) force = false;
  if (force) lastForced = Date.now();
  if (!force && cache.data && Date.now() - cache.at < CACHE_MS) return cache.data;
  if (cache.inflight) return cache.inflight;
  cache.inflight = (async () => {
    const [mealieR, actualR, homeboxR] = await Promise.all([section(mealie), section(actual), section(homebox)]);
    const data = { generatedAt: new Date().toISOString(), cacheSeconds: CACHE_MS / 1000, mealie: mealieR, actual: actualR, homebox: homeboxR };
    cache = { at: Date.now(), data, inflight: null };
    return data;
  })();
  return cache.inflight;
}

const MIME = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png",
  ".json": "application/json", ".webmanifest": "application/manifest+json", ".jpg": "image/jpeg", ".ico": "image/x-icon" };
// per-person view of the shared summary cache: a section the person's groups don't cover becomes {error: "no access"}
const SECTION_ACCESS = { actual: "actual-budget", mealie: "mealie", homebox: "homebox" };
function summaryFor(data, user) {
  const out = { ...data };
  for (const [k, slug] of Object.entries(SECTION_ACCESS)) if (!user.groups.includes(`app-${slug}`)) out[k] = { error: "no access" };
  return out;
}

// the desktop may be framed only by itself (week.html inside a window); scripts only from this origin
const CSP = [
  "default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", `img-src 'self' https://cloud.${DOMAIN} data:`,
  "frame-src 'self' https:", "connect-src 'self'", "frame-ancestors 'self'", "base-uri 'none'", "form-action 'self'",
].join("; ");
const BASE_HEADERS = { "X-Content-Type-Options": "nosniff", "Content-Security-Policy": CSP, "Referrer-Policy": "strict-origin-when-cross-origin" };
const json = (res, data, code = 200) => res.writeHead(code, { ...BASE_HEADERS, "Content-Type": "application/json", "Cache-Control": "no-store" }).end(JSON.stringify(data));
const deny = (res) => json(res, { error: "forbidden" }, 403);

// ---------- gate ----------
const GATE = env("DASH_GATE_SECRET");
const GATE_BUF = Buffer.from(GATE);
if (!GATE) log.warn("DASH_GATE_SECRET is not set: any container on the proxy network can load the desktop; /api/* still needs X-authentik-username");
function gateOk(req) {
  if (!GATE) return true;
  const got = Buffer.from(String(req.headers["x-dash-gate"] || ""));
  // timingSafeEqual needs equal lengths; compare against itself on a mismatch so the timing doesn't depend on the length
  return crypto.timingSafeEqual(got.length === GATE_BUF.length ? got : GATE_BUF, GATE_BUF) && got.length === GATE_BUF.length;
}

// the page's brand images (favicon, Start button) come from https://cloud.<domain>/brand; files say {domain}
const TEMPLATED = new Set([".html", ".webmanifest", ".css"]);   // desktop.css: wallpaper from cloud.{domain}
http
  .createServer(async (req, res) => {
    const url = new URL(req.url, "http://x");
    try {
      if (url.pathname === "/healthz") return res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({ ok: true, version: VERSION, uptimeSeconds: Math.round((Date.now() - STARTED) / 1000) }));
      if (!gateOk(req)) return deny(res);
      if (url.pathname.startsWith("/api/")) {
        const user = me(req);
        if (!user.username) return deny(res);
        if (url.pathname === "/api/me") return json(res, user);
        if (url.pathname === "/api/apps") return json(res, appsFor(user));
        if (url.pathname === "/api/summary") return json(res, summaryFor(await summary(url.searchParams.has("refresh")), user));
        if (url.pathname === "/api/diagnostics") return isAdmin(user) ? json(res, diagnostics()) : deny(res);
        return json(res, { error: "not found" }, 404);
      }
      const file = path.join(__dirname, "public", url.pathname === "/" ? "index.html" : path.normalize(url.pathname).replace(/^(\.\.[/\\])+/, ""));
      if (!file.startsWith(path.join(__dirname, "public"))) return res.writeHead(403).end();
      if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) return res.writeHead(404).end("not found");
      const longCache = file.includes(`${path.sep}icons${path.sep}`);
      const ext = path.extname(file);
      res.writeHead(200, { ...BASE_HEADERS, "Content-Type": MIME[ext] || "application/octet-stream", "Cache-Control": longCache ? "public, max-age=86400" : "no-cache" });
      if (TEMPLATED.has(ext)) return res.end(fs.readFileSync(file, "utf8").replaceAll("{domain}", DOMAIN));
      fs.createReadStream(file).pipe(res);
    } catch (e) {
      noteError(`${req.method} ${url.pathname}`, e);
      log.error(`${req.method} ${url.pathname}: ${e?.stack || e}`);
      if (!res.headersSent) res.writeHead(500, { "Content-Type": "text/plain" });
      res.end("internal error");
    }
  })
  .listen(PORT, HOST, () => {
    log.info(`heardos-dash ${VERSION} listening on ${HOST}:${PORT}`, { domain: DOMAIN, cacheSeconds: CACHE_MS / 1000, gate: GATE ? "on" : "OFF", adminGroup: ADMIN_GROUP });
    for (const c of diagnostics().checks) if (!c.ok) log.warn(`check: ${c.label}: ${c.hint}`);
  });

// warm the cache at start so the first page view is fast
summary().then((d) => log.info("summary warmed", Object.fromEntries(Object.entries(health).map(([k, v]) => [k, v.state])))).catch(() => {});

// docker stop: exit at once instead of waiting 10 s for SIGKILL
for (const sig of ["SIGTERM", "SIGINT"]) process.on(sig, () => process.exit(0));
