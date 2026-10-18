// HeardOS dashboard backend (preliminary, 2026-10-13; desktop shell 2026-10-18).
// The page is now a web desktop (public/index.html + desktop.js): apps open signed in, in windows, through the
// Authentik gate in front of this container. /api/me and /api/apps come from the X-authentik-* headers that gate adds;
// /api/apps filters apps.json by the person's app-<slug> groups. The old summary page lives on as public/week.html.
// Pulls: Mealie (meal plan + shopping lists), Actual Budget (month summary, accounts, upcoming schedules),
// Homebox (inventory totals, locations, recent items). Each source is optional: missing env => section
// reports "not configured"; a failing source reports its error instead of taking the page down.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 8080);
const CACHE_MS = Number(process.env.CACHE_SECONDS || 180) * 1000;
const env = (k) => (process.env[k] || "").trim();

// ---------- helpers ----------
async function getJSON(url, headers = {}, timeoutMs = 15000) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const r = await fetch(url, { headers, signal: ac.signal });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText} for ${url.replace(/\/\/.*@/, "//")}`);
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
  if (!base || !token) return { error: "not configured (MEALIE_URL / MEALIE_TOKEN)" };
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
  if (!serverURL || !password || !syncId) return { error: "not configured (ACTUAL_URL / ACTUAL_PASSWORD / ACTUAL_SYNC_ID)" };
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
  if (!base || !key) return { error: "not configured (HOMEBOX_URL / HOMEBOX_API_KEY)" };
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
const SHOW_ALL = env("DESKTOP_SHOW_ALL") === "1";   // local testing only: no gate headers -> show everything
const APPS_FILE = env("APPS_FILE") || path.join(__dirname, "apps.json");
function me(req) {
  const h = (k) => String(req.headers[k] || "");
  const groups = h("x-authentik-groups").split("|").map((g) => g.trim()).filter(Boolean);
  return { username: h("x-authentik-username"), name: h("x-authentik-name") || h("x-authentik-username"), email: h("x-authentik-email"), groups };
}
function appsFor(user) {
  const cat = JSON.parse(fs.readFileSync(APPS_FILE, "utf8"));
  const sub = (s) => (typeof s === "string" ? s.replaceAll("{domain}", DOMAIN) : s);
  const can = (a) => (SHOW_ALL && !user.username) || !a.access || user.groups.includes(`app-${a.access}`);
  const apps = cat.apps.filter(can).map((a) => {
    const icon = a.icon.startsWith("builtin:") ? `/icons/${a.icon.slice(8)}.svg` : `/icons/${a.icon}.png`;
    return { id: a.id, name: a.name, description: a.description, folder: a.folder, icon, url: sub(a.url), embed: a.embed !== false, w: a.w, h: a.h };
  });
  return { domain: DOMAIN, folders: cat.folders, apps,
    links: { account: `https://auth.${DOMAIN}/if/user/`, signOut: "/outpost.goauthentik.io/sign_out", brand: `https://cloud.${DOMAIN}/brand` } };
}

// ---------- cache + server ----------
let cache = { at: 0, data: null, inflight: null };
async function section(fn) {
  try {
    return await fn();
  } catch (e) {
    return { error: String(e.message || e) };
  }
}
async function summary(force = false) {
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
// the desktop may be framed only by itself (week.html inside a window); everything else is the browser's default
const BASE_HEADERS = { "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "frame-ancestors 'self'", "Referrer-Policy": "strict-origin-when-cross-origin" };
const json = (res, data) => res.writeHead(200, { ...BASE_HEADERS, "Content-Type": "application/json", "Cache-Control": "no-store" }).end(JSON.stringify(data));
http
  .createServer(async (req, res) => {
    const url = new URL(req.url, "http://x");
    try {
      if (url.pathname === "/healthz") return res.writeHead(200, { "Content-Type": "text/plain" }).end("ok");
      if (url.pathname === "/api/me") return json(res, me(req));
      if (url.pathname === "/api/apps") return json(res, appsFor(me(req)));
      if (url.pathname === "/api/summary") {
        const data = await summary(url.searchParams.has("refresh"));
        return json(res, data);
      }
      const file = path.join(__dirname, "public", url.pathname === "/" ? "index.html" : path.normalize(url.pathname).replace(/^(\.\.[/\\])+/, ""));
      if (!file.startsWith(path.join(__dirname, "public"))) return res.writeHead(403).end();
      if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) return res.writeHead(404).end("not found");
      const longCache = file.includes(`${path.sep}icons${path.sep}`);
      res.writeHead(200, { ...BASE_HEADERS, "Content-Type": MIME[path.extname(file)] || "application/octet-stream", "Cache-Control": longCache ? "public, max-age=86400" : "no-cache" });
      fs.createReadStream(file).pipe(res);
    } catch (e) {
      res.writeHead(500, { "Content-Type": "text/plain" }).end(String(e.message || e));
    }
  })
  .listen(PORT, () => console.log(`heardos-dash listening on :${PORT}, cache ${CACHE_MS / 1000}s`));

// warm the cache at start so the first page view is fast
summary().catch(() => {});

// docker stop: exit at once instead of waiting 10 s for SIGKILL
for (const sig of ["SIGTERM", "SIGINT"]) process.on(sig, () => process.exit(0));
