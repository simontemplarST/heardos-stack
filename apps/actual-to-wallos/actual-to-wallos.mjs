#!/usr/bin/env node
// actual-to-wallos.mjs — one-way sync of monthly subscriptions FROM Actual Budget TO Wallos.
//
// Runs inside the heardos-dash image (node 22 + @actual-app/api 26.x) via actual-to-wallos.sh, scheduled by CronMaster.
// Two sources, both optional:
//   1. Actual *schedules* that recur with a cycle in SYNC_CYCLES and have a negative amount (marker actual-schedule:<id>).
//   2. Inferred from *transactions*: expense transactions whose category belongs to one of the SUBSCRIPTION_GROUPS
//      category groups (default "Subscriptions"), grouped by payee; a payee charged at a regular cadence (monthly by
//      default: median gap 26–35 days, at least MIN_CHARGES charges, last charge within STALE_CYCLES cycles) becomes
//      a subscription priced at its latest charge (marker actual-payee:<payeeId>).
// Wallos subscriptions are matched by the marker in their notes, so re-runs update instead of duplicating. Subscriptions
// whose source disappears (schedule deleted, payee no longer charging) are marked inactive — never deleted.
//
// Env (see actual-to-wallos.env): ACTUAL_URL, ACTUAL_SYNC_ID (sync id or file id), ACTUAL_DATA_DIR, ACTUAL_SESSION_TOKEN
//   or ACTUAL_API_PASSWORD, WALLOS_URL, WALLOS_API_KEY, WALLOS_CURRENCY (default USD), SYNC_CYCLES (default monthly; monthly,quarterly,yearly,weekly,daily),
//   SUBSCRIPTION_GROUPS (default "Subscriptions"; comma list of Actual category-group names), MIN_CHARGES (2),
//   STALE_CYCLES (2), LOOKBACK_DAYS (400)
//   LOGO_SEARCH (1 = look up a logo on the internet through Wallos's own image search for new subscriptions and for
//   existing synced ones that still have no logo; 0 = never touch logos)
// Flags: --dry-run (print the plan, change nothing), --verbose
import fs from 'node:fs';

const args = new Set(process.argv.slice(2));
const DRY = args.has('--dry-run'); const VERBOSE = args.has('--verbose');
const env = (k, d) => (process.env[k] ?? d);
const die = (m) => { console.error('ERROR: ' + m); process.exit(2); };
const vlog = (...a) => { if (VERBOSE) console.log(...a); };

const ACTUAL_URL = env('ACTUAL_URL'); const SYNC_ID = env('ACTUAL_SYNC_ID'); const DATA_DIR = env('ACTUAL_DATA_DIR', '/data/actual-cache');
const SESSION = env('ACTUAL_SESSION_TOKEN', ''); const PASSWORD = env('ACTUAL_API_PASSWORD', '');
const WALLOS_URL = (env('WALLOS_URL') || '').replace(/\/$/, ''); const WALLOS_KEY = env('WALLOS_API_KEY', '');
const CYCLES = new Set(env('SYNC_CYCLES', 'monthly').split(',').map((s) => s.trim().toLowerCase()).filter(Boolean));
const GROUPS = new Set(env('SUBSCRIPTION_GROUPS', 'Subscriptions').split(',').map((s) => s.trim().toLowerCase()).filter(Boolean));
const MIN_CHARGES = Number(env('MIN_CHARGES', '2')); const STALE_CYCLES = Number(env('STALE_CYCLES', '2')); const LOOKBACK = Number(env('LOOKBACK_DAYS', '400'));
const CURRENCY = env('WALLOS_CURRENCY', 'USD').toUpperCase(); const LOGO_SEARCH = env('LOGO_SEARCH', '1') !== '0';

if (!ACTUAL_URL || !SYNC_ID) die('ACTUAL_URL and ACTUAL_SYNC_ID are required');
if (!SESSION && !PASSWORD) die('no Actual credential: set ACTUAL_SESSION_TOKEN (copy localStorage "user-token" from the Actual web app) or ACTUAL_API_PASSWORD');
if (!WALLOS_URL || !WALLOS_KEY) die('WALLOS_URL and WALLOS_API_KEY are required (Wallos -> Settings -> API key)');

// cadence definitions: [cycle code for Wallos (1 days, 2 weeks, 3 months, 4 years), nominal days, min gap, max gap, Wallos frequency]
const CADENCE = { daily: [1, 1, 1, 1, 1], weekly: [2, 7, 6, 8, 1], monthly: [3, 30, 26, 35, 1], quarterly: [3, 91, 80, 100, 3], yearly: [4, 365, 355, 375, 1] };

// ---------- Wallos ----------
async function wallos(path, params = {}, method = 'GET') {
  const body = new URLSearchParams({ api_key: WALLOS_KEY, ...params });
  const url = WALLOS_URL + path + (method === 'GET' ? '?' + body.toString() : '');
  const r = await fetch(url, method === 'GET' ? {} : { method: 'POST', body, headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
  const text = await r.text();
  let j; try { j = JSON.parse(text); } catch { throw new Error(`Wallos ${path}: non-JSON response (${r.status}): ${text.slice(0, 200)}`); }
  if (j.success === false) throw new Error(`Wallos ${path}: ${j.title || ''} ${j.message || ''}`.trim());
  return j;
}
// Internet logo lookup via Wallos's own search endpoint (Brave/DuckDuckGo image search for "<name> logo"); returns a URL
// that Wallos then downloads itself through logo_url (same path its "Search logo" button uses). Null when nothing usable.
// Optional overrides file next to the script (LOGO_OVERRIDES, default /app/sync/actual-to-wallos-logos.json): {"<subscription name>": "<image url>"}
// for brands where the image search picks badly; a Google favicon URL (https://www.google.com/s2/favicons?domain=X&sz=256) is a safe choice.
let logoOverrides = {};
try { logoOverrides = JSON.parse(fs.readFileSync(env('LOGO_OVERRIDES', '/app/sync/actual-to-wallos-logos.json'), 'utf8')); } catch { /* none */ }
async function findLogo(name) {
  if (!LOGO_SEARCH) return null;
  const override = Object.entries(logoOverrides).find(([k]) => k.toLowerCase() === name.toLowerCase())?.[1];
  if (override) return override;
  try {
    const r = await fetch(`${WALLOS_URL}/endpoints/logos/search.php?search=${encodeURIComponent(name)}`, { signal: AbortSignal.timeout(45000) });
    const j = await r.json(); const hit = (j.results || []).find((x) => /^https:\/\//.test(x.image || ''));
    return hit ? hit.image : null;
  } catch (e) { vlog(`logo search failed for ${name}: ${e.message}`); return null; }
}
const [cats, pms, curs, subsRes, hh] = await Promise.all([
  wallos('/api/categories/get_categories.php'), wallos('/api/payment_methods/get_payment_methods.php'),
  wallos('/api/currencies/get_currencies.php'), wallos('/api/subscriptions/get_subscriptions.php'),
  wallos('/api/household/get_household.php').catch(() => ({ household: [] })),
]);
// payer: the Wallos UI always sets one (the API does not), and subscriptions.php / the details popup warn on a NULL payer
const PAYER_ID = String(env('WALLOS_PAYER_ID', '') || [...(hh.household || [])].sort((a, b) => a.id - b.id)[0]?.id || 1);
const catByName = new Map((cats.categories || []).map((c) => [c.name.toLowerCase(), c.id]));
const pmByName = new Map((pms.payment_methods || []).map((p) => [p.name.toLowerCase(), p.id]));
const currency = (curs.currencies || []).find((c) => (c.code || '').toUpperCase() === CURRENCY);
const currencyId = currency ? currency.id : curs.main_currency;
const existing = new Map(); // marker -> wallos subscription
for (const s of subsRes.subscriptions || []) { const m = /(actual-(?:schedule|payee):[0-9a-f-]{8,})/i.exec(s.notes || ''); if (m) existing.set(m[1].toLowerCase(), s); }

// Actual category name -> Wallos category; then keyword fallback; then "No category"
const CAT_MAP = {
  'streaming': 'entertainment', 'entertainment & events': 'entertainment', 'music & audio': 'music', 'memberships': 'entertainment',
  'software & apps': 'software', 'ai tools': 'software', 'gaming': 'gaming', 'cloud & hosting': 'cloud services', 'domains & dns': 'cloud services',
  'news & reading': 'news & magazines', 'internet': 'utilities', 'mobile phone': 'utilities', 'electricity': 'utilities', 'natural gas': 'utilities',
  'water & sewer': 'utilities', 'trash': 'utilities', 'home security': 'utilities', 'gym': 'health & wellbeing', 'health': 'health & wellbeing',
  'medical': 'health & wellbeing', 'auto & home': 'insurance', 'life & disability': 'insurance', 'charity & donations': 'charity & donations',
  'education': 'education', 'groceries': 'food & beverages', 'delivery': 'food & beverages', 'coffee': 'food & beverages', 'electronics': 'technology',
};
const CAT_RULES = [
  [/stream|netflix|hulu|disney|hbo|peacock|paramount|youtube|tv|video|movie|entertain|prime/i, 'entertainment'],
  [/spotify|apple music|music|pandora|tidal|audible|podcast/i, 'music'],
  [/electric|gas\b|water|sewer|trash|garbage|utilit|power|energy|internet|phone|mobile|cell|wireless|cable/i, 'utilities'],
  [/food|grocer|meal|coffee|restaurant|dining/i, 'food & beverages'],
  [/gym|fitness|health|medical|dental|doctor|therapy|wellness|pharm/i, 'health & wellbeing'],
  [/software|license|saas|app\b|adobe|microsoft|office|365|github|jetbrains|rocket money/i, 'software'],
  [/cloud|storage|backup|hosting|server|vps|domain|dns|aws|azure|google one|icloud|dropbox/i, 'cloud services'],
  [/insur/i, 'insurance'], [/bank|fee|loan|interest|credit/i, 'banking'], [/car|auto|fuel|transit|parking|toll|transport|uber|lyft/i, 'transport'],
  [/school|tuition|education|course|class|learn/i, 'education'], [/game|gaming|xbox|playstation|nintendo|steam/i, 'gaming'],
  [/news|magazine|newspaper|journal/i, 'news & magazines'], [/donat|charity|church|tithe|patreon/i, 'charity & donations'],
  [/tech|electronic|device|hardware/i, 'technology'], [/productiv|notion|todo|calendar/i, 'productivity'],
];
function categoryFor(actualCategory, ...names) {
  const mapped = actualCategory && CAT_MAP[actualCategory.toLowerCase()]; if (mapped && catByName.has(mapped)) return catByName.get(mapped);
  for (const n of [actualCategory, ...names].filter(Boolean)) { const exact = catByName.get(n.toLowerCase()); if (exact) return exact; }
  const hay = [actualCategory, ...names].filter(Boolean).join(' ');
  for (const [re, cat] of CAT_RULES) if (re.test(hay) && catByName.has(cat)) return catByName.get(cat);
  return catByName.get('no category') ?? 1;
}
function paymentMethodFor(accountName = '') {
  const a = accountName.toLowerCase();
  if (/paypal/.test(a)) return pmByName.get('paypal');
  if (/venmo/.test(a)) return pmByName.get('venmo');
  if (/credit|card|visa|master|amex|american express|discover|capital one|sapphire|rewards/.test(a)) return pmByName.get('credit card');
  return pmByName.get('direct debit') ?? pmByName.get('bank transfer') ?? 1;
}
const iso = (d) => d.toISOString().slice(0, 10);
function addCycles(dateStr, cycle, n) { // next date after dateStr, n cycles of the given cadence
  const d = new Date(dateStr + 'T00:00:00Z');
  if (cycle === 'monthly') d.setUTCMonth(d.getUTCMonth() + n); else if (cycle === 'quarterly') d.setUTCMonth(d.getUTCMonth() + 3 * n); else if (cycle === 'yearly') d.setUTCFullYear(d.getUTCFullYear() + n);
  else d.setUTCDate(d.getUTCDate() + n * CADENCE[cycle][1]);
  return iso(d);
}
function nextOnOrAfterToday(lastDate, cycle, interval = 1) {
  const today = iso(new Date()); let n = lastDate;
  for (let i = 0; i < 400 && n < today; i++) n = addCycles(n, cycle, interval);
  return n;
}

// ---------- Actual ----------
const api = await import('@actual-app/api'); const { q, runQuery } = api;
fs.mkdirSync(DATA_DIR, { recursive: true });
try {
  await api.init({ serverURL: ACTUAL_URL, dataDir: DATA_DIR, ...(SESSION ? { sessionToken: SESSION } : { password: PASSWORD }) });
  const files = await api.getBudgets();
  const f = files.find((x) => x.groupId === SYNC_ID || x.id === SYNC_ID || x.cloudFileId === SYNC_ID);
  if (!f) die(`no budget on the server matches ACTUAL_SYNC_ID; server has: ${files.map((x) => `${x.name} (sync id ${x.groupId})`).join(', ') || 'none'}`);
  vlog(`budget: ${f.name} (sync id ${f.groupId})`);
  await api.downloadBudget(f.groupId);
} catch (e) {
  const msg = String(e?.message || e);
  if (/redirect|openid|token|unauthorized|invalid-password/i.test(msg)) die(`Actual login failed (${msg}). The server is OpenID-only: paste a fresh localStorage "user-token" from the Actual web app into ACTUAL_SESSION_TOKEN.`);
  throw e;
}
const plan = []; // { marker, fields }
try {
  const [schedules, payees, accounts, categories, groups, rules] = await Promise.all([
    api.getSchedules(), api.getPayees(), api.getAccounts(), api.getCategories(), api.getCategoryGroups(), api.getRules()]);
  const payeeName = new Map(payees.map((p) => [p.id, p.name])); const accountName = new Map(accounts.map((a) => [a.id, a.name]));
  const categoryName = new Map(categories.map((c) => [c.id, c.name])); const ruleById = new Map(rules.map((r) => [r.id, r]));
  const groupName = new Map(groups.map((g) => [g.id, g.name]));
  const subCategoryIds = new Set(categories.filter((c) => GROUPS.has(String(groupName.get(c.group_id) || '').toLowerCase())).map((c) => c.id));
  const price = (cents) => (Math.abs(cents) / 100).toFixed(2);
  const build = ({ marker, name, cents, cycle, interval, nextDate, startDate, account, actualCat, sourceNote }) => ({
    marker, fields: {
      name, price: price(cents), currency_id: String(currencyId), frequency: String(interval), cycle: String(CADENCE[cycle][0]),
      next_payment: nextDate, start_date: startDate, auto_renew: '1', payment_method_id: String(paymentMethodFor(account)),
      category_id: String(categoryFor(actualCat, name)), payer_user_id: PAYER_ID, inactive: '0',
      notes: `Synced from Actual Budget: ${sourceNote} (category ${actualCat || 'none'}, account ${account || '?'}). ${marker}`,
    },
  });

  // 1. schedules
  const amountOf = (a) => (a == null ? 0 : typeof a === 'number' ? a : typeof a === 'object' ? Math.min(Number(a.num1 ?? 0), Number(a.num2 ?? a.num1 ?? 0)) : Number(a));
  for (const s of schedules) {
    if (s.tombstone || s.completed) continue;
    const d = s._date; const freq = d && typeof d === 'object' && d.frequency ? String(d.frequency).toLowerCase() : null;
    if (!freq) { vlog(`skip schedule (one-time): ${s.name}`); continue; }
    if (!CYCLES.has(freq)) { vlog(`skip schedule (${freq}): ${s.name}`); continue; }
    const cents = amountOf(s._amount); if (cents >= 0) { vlog(`skip schedule (income/zero): ${s.name}`); continue; }
    const catAction = ruleById.get(s.rule)?.actions?.find((a) => a.field === 'category' && a.op === 'set');
    plan.push(build({ marker: `actual-schedule:${s.id}`, name: (s.name || payeeName.get(s._payee) || 'Unnamed schedule').trim(), cents, cycle: freq,
      interval: Number(d.interval || 1), nextDate: String(s.next_date || '').slice(0, 10), startDate: String(d.start || s.next_date || '').slice(0, 10),
      account: accountName.get(s._account), actualCat: catAction ? categoryName.get(catAction.value) : undefined, sourceNote: 'schedule' }));
  }
  vlog(`schedules: ${schedules.length} total, ${plan.length} usable`);

  // 2. inferred from transactions in the subscription category groups
  const since = iso(new Date(Date.now() - LOOKBACK * 864e5));
  const tx = (await runQuery(q('transactions').filter({ date: { $gte: since }, amount: { $lt: 0 }, 'category.id': { $oneof: [...subCategoryIds] } })
    .select(['date', 'amount', 'payee', 'payee.name', 'category', 'category.name', 'account.name']))).data;
  const byPayee = new Map();
  for (const t of tx) { if (!t.payee) continue; (byPayee.get(t.payee) || byPayee.set(t.payee, []).get(t.payee)).push(t); }
  vlog(`transactions in groups [${[...GROUPS].join(', ')}] since ${since}: ${tx.length} across ${byPayee.size} payees`);
  const today = iso(new Date());
  for (const [payeeId, ts] of byPayee) {
    ts.sort((a, b) => a.date.localeCompare(b.date));
    const name = ts[ts.length - 1]['payee.name'] || payeeName.get(payeeId) || 'Unknown payee';
    if (ts.length < MIN_CHARGES) { vlog(`skip payee (only ${ts.length} charge): ${name}`); continue; }
    const gaps = ts.slice(1).map((t, i) => (new Date(t.date) - new Date(ts[i].date)) / 864e5).sort((a, b) => a - b);
    const median = gaps[Math.floor(gaps.length / 2)];
    const cycle = Object.entries(CADENCE).find(([c, [, , lo, hi]]) => CYCLES.has(c) && median >= lo && median <= hi)?.[0];
    if (!cycle) { vlog(`skip payee (median gap ${Math.round(median)}d is not ${[...CYCLES].join('/')}): ${name}`); continue; }
    const last = ts[ts.length - 1]; const staleAfter = addCycles(last.date, cycle, STALE_CYCLES);
    if (staleAfter < today) { vlog(`skip payee (stale: last charge ${last.date}, ${STALE_CYCLES} cycles passed): ${name}`); continue; }
    plan.push(build({ marker: `actual-payee:${payeeId}`, name, cents: last.amount, cycle, interval: CADENCE[cycle][4], nextDate: nextOnOrAfterToday(last.date, cycle),
      startDate: ts[0].date, account: last['account.name'], actualCat: last['category.name'], sourceNote: `${ts.length} charges, median gap ${Math.round(median)} days, latest ${last.date}` }));
  }
} finally { await api.shutdown(); }

// ---------- apply ----------
let added = 0, updated = 0, unchanged = 0, deactivated = 0;
const seen = new Set();
for (const p of plan) {
  const key = p.marker.toLowerCase(); seen.add(key); const e = existing.get(key);
  if (!e) {
    const logo = DRY ? null : await findLogo(p.fields.name);
    console.log(`${DRY ? 'would add' : 'add'}: ${p.fields.name} — ${p.fields.price} ${CURRENCY} every ${p.fields.frequency} × cycle ${p.fields.cycle}, next ${p.fields.next_payment}${logo ? ' (with logo)' : ''}`);
    if (!DRY) { const res = await wallos('/api/subscriptions/set_subscriptions.php', { action: 'add', ...p.fields, ...(logo ? { logo_url: logo } : {}) }, 'POST'); if (res.logo_warning) console.log(`  logo not saved: ${res.logo_warning}`); }
    added++; continue;
  }
  const diff = [];
  for (const k of ['name', 'price', 'currency_id', 'frequency', 'cycle', 'next_payment', 'inactive', 'payer_user_id']) {
    const cur = k === 'price' ? Number(e[k]).toFixed(2) : String(e[k] ?? '');
    if (cur !== p.fields[k]) diff.push(`${k}: ${cur} -> ${p.fields[k]}`);
  }
  let logo = null;
  if (!(e.logo || '').trim() && LOGO_SEARCH) { logo = DRY ? 'search' : await findLogo(p.fields.name); if (logo) diff.push('logo: none -> found online'); }
  if (!diff.length) { unchanged++; continue; }
  console.log(`${DRY ? 'would update' : 'update'}: ${p.fields.name} (${diff.join(', ')})`);
  if (!DRY) { const res = await wallos('/api/subscriptions/set_subscriptions.php', { action: 'edit', id: String(e.id), ...p.fields, ...(logo ? { logo_url: logo } : {}) }, 'POST'); if (res.logo_warning) console.log(`  logo not saved: ${res.logo_warning}`); }
  updated++;
}
for (const [marker, e] of existing) {
  if (seen.has(marker) || Number(e.inactive) === 1) continue;
  console.log(`${DRY ? 'would deactivate' : 'deactivate'} (no longer seen in Actual): ${e.name}`);
  if (!DRY) await wallos('/api/subscriptions/set_subscriptions.php', { action: 'edit', id: String(e.id), inactive: '1' }, 'POST');
  deactivated++;
}
console.log(`${DRY ? 'DRY RUN — ' : ''}subscriptions found in Actual: ${plan.length}; added ${added}, updated ${updated}, unchanged ${unchanged}, deactivated ${deactivated}`);
