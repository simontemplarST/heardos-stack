// This week (week.html): meals, shopping, budget and inventory from /api/summary. External file so the page's CSP can
// be script-src 'self'. Sections the person has no access to come back as {error: "no access"} and are left out.
"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const money = (n) => (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
const dayName = (iso) => { const d = new Date(iso + "T12:00:00"); return d.toLocaleDateString(undefined, {weekday: "short", month: "short", day: "numeric"}); };
const today = new Date().toISOString().slice(0, 10);
function card(title, link, body) { return `<section class="card"><h2>${esc(title)}${link ? ` <a href="${esc(link)}">open ↗</a>` : ""}</h2>${body}</section>`; }
const noAccess = (sec) => sec?.error === "no access";   // the person isn't in that app's group: the card is hidden
function errOr(sec, fn) { return sec.error ? `<p class="err">${esc(sec.error)}</p>` : fn(sec); }

function mealsCard(m) {
  return card("This week's meals", m.link && m.link + "/g/home/mealplan/planner", errOr(m, (m) => {
    if (!m.meals.length) return `<p class="muted">Nothing planned for the next 7 days.</p>`;
    const byDay = {};
    m.meals.forEach((e) => (byDay[e.date] ||= []).push(e));
    return `<table>${Object.keys(byDay).sort().map((d) => `<tr class="${d === today ? "today" : ""}"><td class="day">${esc(dayName(d))}</td><td>${byDay[d].map((e) => `<div><span class="muted small">${esc(e.type)}</span> ${esc(e.title)}</div>`).join("")}</td></tr>`).join("")}</table>`;
  }));
}
function shoppingCard(m) {
  return card("Shopping list", m.link && m.link + "/shopping-lists", errOr(m, (m) => {
    if (!m.shopping.length) return `<p class="muted">No shopping lists.</p>`;
    return m.shopping.map((l) => `<p class="small"><b>${esc(l.name)}</b> <span class="muted">${l.open.length} to buy, ${l.checked} done</span></p>` +
      (l.open.length ? `<ul class="list">${l.open.map((i) => `<li>${esc(i.text)}${i.label ? `<span class="tag">${esc(i.label)}</span>` : ""}</li>`).join("")}</ul>` : "")).join("");
  }));
}
function budgetCard(a) {
  return card(`Budget · ${a.month || ""}`, a.link, errOr(a, (a) => {
    const kpi = (v, l, cls = "") => `<div class="kpi"><div class="v ${cls}">${money(v)}</div><div class="l">${l}</div></div>`;
    const cats = a.categories.slice(0, 10).map((c) => {
      const pct = c.budgeted > 0 ? Math.min(100, Math.abs(c.spent) / c.budgeted * 100) : (c.spent ? 100 : 0);
      return `<tr><td>${esc(c.name)}<div class="bar"><i class="${c.balance < 0 ? "over" : ""}" style="width:${pct.toFixed(0)}%"></i></div></td><td class="num muted">${money(c.budgeted)}</td><td class="num">${money(Math.abs(c.spent))}</td><td class="num ${c.balance < 0 ? "neg" : "pos"}">${money(c.balance)}</td></tr>`;
    }).join("");
    return `<div class="kpis">${kpi(a.toBudget, "To budget", a.toBudget < 0 ? "neg" : "pos")}${kpi(a.totalIncome, "Income")}${kpi(Math.abs(a.totalSpent), "Spent")}${kpi(a.totalBudgeted, "Budgeted")}</div>` +
      (cats ? `<table><tr><th>Category</th><th class="num">Budgeted</th><th class="num">Spent</th><th class="num">Left</th></tr>${cats}</table>` : `<p class="muted">No categories yet.</p>`);
  }));
}
function accountsCard(a) {
  return card("Accounts & upcoming", a.link, errOr(a, (a) => {
    const acc = a.accounts.map((x) => `<tr><td>${esc(x.name)}${x.offbudget ? ` <span class="muted small">off budget</span>` : ""}</td><td class="num ${x.balance < 0 ? "neg" : ""}">${money(x.balance)}</td></tr>`).join("");
    const sch = a.schedules.map((s) => `<li><span class="day">${esc(dayName(s.date))}</span> ${esc(s.name)}<span class="tag ${s.amount < 0 ? "neg" : "pos"}">${money(s.amount)}</span></li>`).join("");
    return (acc ? `<table>${acc}</table>` : `<p class="muted">No accounts yet.</p>`) + `<p class="small muted" style="margin:.8rem 0 .3rem">Next 14 days</p>` + (sch ? `<ul class="list">${sch}</ul>` : `<p class="muted small">Nothing scheduled.</p>`);
  }));
}
function inventoryCard(h) {
  return card("Inventory", h.link, errOr(h, (h) => {
    const t = h.totals;
    const kpi = (v, l) => `<div class="kpi"><div class="v">${esc(v)}</div><div class="l">${l}</div></div>`;
    const locs = h.locations.map((l) => `<li>${esc(l.name)}<span class="tag">${l.total} items</span></li>`).join("");
    const rec = h.recent.map((i) => `<li>${esc(i.name)}${i.parent ? ` <span class="muted small">· ${esc(i.parent)}</span>` : ""}<span class="tag">${i.qty > 1 ? "×" + i.qty + " · " : ""}${esc(i.created)}</span></li>`).join("");
    return `<div class="kpis">${kpi(t.items, "Items")}${kpi(t.locations, "Locations")}${kpi(money(t.value || 0), "Total value")}${kpi(t.withWarranty, "Under warranty")}</div>` +
      `<p class="small muted" style="margin:.4rem 0 .2rem">Where things are</p><ul class="list">${locs || "<li class='muted'>No locations</li>"}</ul>` +
      `<p class="small muted" style="margin:.8rem 0 .2rem">Recently added</p><ul class="list">${rec || "<li class='muted'>Nothing yet</li>"}</ul>`;
  }));
}
async function load(refresh) {
  $("#stamp").textContent = "loading…";
  try {
    const r = await fetch("/api/summary" + (refresh ? "?refresh=1" : ""), {cache: "no-store"});
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const html = (noAccess(d.mealie) ? "" : mealsCard(d.mealie) + shoppingCard(d.mealie)) +
      (noAccess(d.actual) ? "" : budgetCard(d.actual) + accountsCard(d.actual)) +
      (noAccess(d.homebox) ? "" : inventoryCard(d.homebox));
    $("#main").innerHTML = html || `<p class="muted">Nothing to show here yet: you don't have access to meals, the budget or the inventory.</p>`;
    $("#stamp").textContent = "updated " + new Date(d.generatedAt).toLocaleTimeString(undefined, {hour: "numeric", minute: "2-digit"});
  } catch (e) {
    $("#stamp").textContent = "failed: " + e.message;
  }
}
$("#refresh").addEventListener("click", () => load(true));
// inside a HeardOS desktop window: "open" links ask the desktop to open the app in its own window
if (window.parent !== window) {
  document.body.classList.add("in-desktop");
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (!a) return;
    e.preventDefault();
    window.parent.postMessage({ type: "heardos:open-url", url: a.href }, location.origin);
  });
}
load();
setInterval(() => load(false), 5 * 60 * 1000);
