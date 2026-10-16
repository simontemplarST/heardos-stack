// actual-uncat.mjs — READ-ONLY: list uncategorized on-budget transactions + the category list + payee history.
// Runs inside heardos-dash image on CT 108 (same pattern as actual-to-wallos.sh). Prints JSON to stdout.
import api from '@actual-app/api';
const env = (k, d) => (process.env[k] ?? d);
const dir = env('ACTUAL_DATA_DIR', '/data/actual-cache');
await api.init({ serverURL: env('ACTUAL_URL'), dataDir: dir, ...(env('ACTUAL_SESSION_TOKEN') ? { sessionToken: env('ACTUAL_SESSION_TOKEN') } : { password: env('ACTUAL_API_PASSWORD') }) });
const budgets = await api.getBudgets();
const want = env('ACTUAL_SYNC_ID');
const f = budgets.find(b => b.id === want || b.groupId === want || b.cloudFileId === want) || budgets[0];
await api.downloadBudget(f.groupId || f.cloudFileId || f.id);
const groups = await api.getCategoryGroups();
const cats = {}; for (const g of groups) for (const c of (g.categories || [])) cats[c.id] = { name: c.name, group: g.name, income: !!g.is_income, hidden: !!c.hidden };
const payees = Object.fromEntries((await api.getPayees()).map(p => [p.id, p]));
const accounts = (await api.getAccounts());
const today = new Date().toISOString().slice(0, 10);
const uncat = []; const hist = {};
for (const a of accounts) {
  if (a.offbudget || a.closed) continue;
  const txs = await api.getTransactions(a.id, '2000-01-01', today);
  for (const t of txs) {
    if (t.is_parent) continue;
    if (t.transfer_id) continue;
    const p = payees[t.payee]; const pname = p?.name || '';
    if (/^starting balance$/i.test(pname)) continue;
    if (!t.category) uncat.push({ id: t.id, date: t.date, account: a.name, payee: pname, imported: t.imported_payee || '', notes: t.notes || '', amount: t.amount / 100 });
    else { const k = pname.toLowerCase(); hist[k] ??= {}; const cn = cats[t.category] ? `${cats[t.category].group}/${cats[t.category].name}` : t.category; hist[k][cn] = (hist[k][cn] || 0) + 1; }
  }
}
const byPayee = {};
for (const t of uncat) { const k = t.payee || t.imported || '(no payee)'; byPayee[k] ??= { count: 0, total: 0, first: t.date, last: t.date, accounts: new Set(), imported: new Set(), notes: new Set(), ids: [] };
  const b = byPayee[k]; b.count++; b.total += t.amount; if (t.date < b.first) b.first = t.date; if (t.date > b.last) b.last = t.date; b.accounts.add(t.account); if (t.imported) b.imported.add(t.imported); if (t.notes) b.notes.add(t.notes.slice(0, 60)); b.ids.push(t.id); }
for (const b of Object.values(byPayee)) { b.total = Math.round(b.total * 100) / 100; b.accounts = [...b.accounts]; b.imported = [...b.imported].slice(0, 3); b.notes = [...b.notes].slice(0, 3); b.history = hist[(Object.keys(byPayee).find(k => byPayee[k] === b) || '').toLowerCase()] || null; }
console.log(JSON.stringify({ budget: f.name, categories: Object.values(cats).filter(c => !c.hidden).map(c => `${c.group}/${c.name}${c.income ? ' [income]' : ''}`), accounts: accounts.map(a => `${a.name}${a.offbudget ? ' (off-budget)' : ''}${a.closed ? ' (closed)' : ''}`), uncategorized_count: uncat.length, by_payee: byPayee }, null, 1));
await api.shutdown();
