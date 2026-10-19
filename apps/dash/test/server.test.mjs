// Server behaviour that must not regress: the gate, who sees which apps, per-person summary filtering, the admin-only
// diagnostics, catalogue validation, page templating and the security headers. Starts server.js as a child process
// with a throwaway apps.json; no network beyond 127.0.0.1. Run: npm test
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const PORT = 18000 + Math.floor(Math.random() * 1000);
const BASE = `http://127.0.0.1:${PORT}`;
const GATE = "test-gate-secret";
let child, dir, logs = "";

const CATALOG = {
  folders: ["Everyday", "Admin"],
  apps: [
    { id: "photos", name: "Photos", folder: "Everyday", icon: "nope", url: "https://photos.{domain}/", access: "immich" },
    { id: "search", name: "Search", folder: "Everyday", icon: "builtin:week", url: "https://search.{domain}/", access: null },
    { id: "photos", name: "Dup", folder: "Everyday", icon: "builtin:week", url: "https://x.{domain}/" },           // duplicate id: skipped
    { id: "lost", name: "Lost", folder: "Nowhere", icon: "builtin:week", url: "https://x.{domain}/", bogus: 1 },     // folder + field warnings
    { name: "Broken", folder: "Everyday", icon: "builtin:week", url: "https://x.{domain}/" },                       // no id: skipped
  ],
};

const get = (p, headers = {}) => fetch(BASE + p, { headers, redirect: "manual" });
const asUser = (groups, extra = {}) => ({ "x-dash-gate": GATE, "x-authentik-username": "alice", "x-authentik-name": "Alice Example", "x-authentik-groups": groups, ...extra });

before(async () => {
  dir = mkdtempSync(path.join(tmpdir(), "dash-test-"));
  writeFileSync(path.join(dir, "apps.json"), JSON.stringify(CATALOG));
  child = spawn(process.execPath, [path.join(here, "..", "server.js")], {
    env: { ...process.env, PORT: String(PORT), DASH_GATE_SECRET: GATE, DOMAIN: "example.test", APPS_FILE: path.join(dir, "apps.json"),
      SUPPORT_EMAIL: "help@example.test", SUPPORT_NAME: "Alice", DASH_ADMIN_GROUP: "admins", CACHE_SECONDS: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.on("data", (d) => (logs += d)); child.stderr.on("data", (d) => (logs += d));
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(BASE + "/healthz")).ok) return; } catch {}
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error("server did not start:\n" + logs);
});
after(() => { child?.kill(); rmSync(dir, { recursive: true, force: true }); });

test("healthz needs no gate and reports the version", async () => {
  const r = await fetch(BASE + "/healthz"); const j = await r.json();
  assert.equal(r.status, 200); assert.equal(j.ok, true); assert.match(j.version, /^\d+\.\d+/);
});

test("gate: everything else is refused without the header, with a wrong one, or with a short one", async () => {
  for (const h of [{}, { "x-dash-gate": "wrong-but-same-length!" }, { "x-dash-gate": "x" }]) {
    assert.equal((await get("/", h)).status, 403);
    assert.equal((await get("/api/apps", { ...h, "x-authentik-username": "alice" })).status, 403);
  }
});

test("api needs a signed-in user even with the gate", async () => {
  assert.equal((await get("/api/me", { "x-dash-gate": GATE })).status, 403);
  const r = await get("/api/me", asUser("app-immich")); assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { username: "alice", name: "Alice Example", email: "", groups: ["app-immich"] });
});

test("apps are filtered by group, templated with the domain, and broken entries are skipped", async () => {
  const j = await (await get("/api/apps", asUser("app-immich|app-dash"))).json();
  assert.deepEqual(j.apps.map((a) => a.id), ["photos", "search", "lost"]);
  assert.equal(j.apps[0].url, "https://photos.example.test/");
  assert.equal(j.apps[0].allow, "fullscreen; clipboard-write; publickey-credentials-get");
  assert.equal(j.admin, false); assert.equal(j.support.email, "help@example.test"); assert.match(j.version, /^\d+\./);
  const none = await (await get("/api/apps", asUser(""))).json();
  assert.deepEqual(none.apps.map((a) => a.id), ["search", "lost"]);   // only the public ones
});

test("summary sections are hidden per person", async () => {
  const j = await (await get("/api/summary", asUser("app-mealie"))).json();
  assert.equal(j.actual.error, "no access"); assert.equal(j.homebox.error, "no access");
  assert.equal(j.mealie.error, "not configured");   // no MEALIE_URL in the test env
});

test("diagnostics: admins only, and it lists the catalogue problems", async () => {
  assert.equal((await get("/api/diagnostics", asUser("app-immich"))).status, 403);
  const r = await get("/api/diagnostics", asUser("admins")); assert.equal(r.status, 200);
  const d = await r.json();
  assert.equal(d.apps, 3);
  const w = d.catalogWarnings.join("\n");
  assert.match(w, /duplicate id/); assert.match(w, /missing id/); assert.match(w, /folder "Nowhere"/); assert.match(w, /unknown field "bogus"/); assert.match(w, /icon file public\/icons\/nope\.png is missing/);
  assert.ok(d.checks.find((c) => c.label.startsWith("Gate secret")).ok);
  assert.ok(!d.checks.find((c) => c.label.startsWith("Mealie")).ok);
  assert.ok(["not configured", "ok", "error", undefined].includes(d.sources.mealie.state));
  assert.equal(typeof d.uptimeSeconds, "number");
});

test("pages carry the security headers and the domain is filled in", async () => {
  const r = await get("/", { "x-dash-gate": GATE }); const html = await r.text();
  assert.equal(r.status, 200);
  assert.match(r.headers.get("content-security-policy"), /frame-ancestors 'self'/);
  assert.match(r.headers.get("content-security-policy"), /img-src 'self' https:\/\/cloud\.example\.test/);
  assert.equal(r.headers.get("x-content-type-options"), "nosniff");
  assert.ok(html.includes("https://cloud.example.test/brand/"), "brand URLs use the domain");
  assert.ok(!html.includes("{domain}"), "no template markers left");
  const css = await (await get("/desktop.css", { "x-dash-gate": GATE })).text();
  assert.ok(css.includes("cloud.example.test/brand/flow-background.jpg"));
});

test("no path traversal and unknown api paths are 404", async () => {
  assert.equal((await get("/../server.js", { "x-dash-gate": GATE })).status, 404);
  assert.equal((await get("/api/nothing", asUser("x"))).status, 404);
});

test("the log is one timestamped line per event", () => {
  assert.match(logs, /^\d{4}-\d\d-\d\dT[\d:.]+Z INFO  heardos-dash \S+ listening/m);
  assert.match(logs, /WARN  apps\.json: apps\[2\] \(photos\): duplicate id; skipped/);
});
