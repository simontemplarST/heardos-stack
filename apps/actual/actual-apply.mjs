// actual-apply.mjs [--apply] — applies plan.json: category sets + card-payment transfer conversions. Dry-run by default.
import api from '@actual-app/api'; import fs from 'fs';
const APPLY=process.argv.includes('--apply'); const env=(k,d)=>process.env[k]??d;
const plan=JSON.parse(fs.readFileSync('/app/sync/plan.json','utf8'));
await api.init({ serverURL: env('ACTUAL_URL'), dataDir: env('ACTUAL_DATA_DIR','/data/actual-cache'), sessionToken: env('ACTUAL_SESSION_TOKEN') });
const budgets=await api.getBudgets(); const want=env('ACTUAL_SYNC_ID');
const f=budgets.find(b=>b.id===want||b.groupId===want||b.cloudFileId===want)||budgets[0];
await api.downloadBudget(f.groupId||f.cloudFileId||f.id);
const groups=await api.getCategoryGroups(); const catId={}; for(const g of groups) for(const c of (g.categories||[])) catId[`${g.name}/${c.name}`]=c.id;
const accounts=await api.getAccounts(); const acctId=Object.fromEntries(accounts.map(a=>[a.name,a.id]));
const payees=await api.getPayees(); const xferPayee={}; for(const p of payees) if(p.transfer_acct) xferPayee[p.transfer_acct]=p.id;
let bad=0;
for(const s of plan.sets) if(!catId[s.cat]){ console.log('UNKNOWN CATEGORY', s.cat); bad++; }
for(const t of plan.transfers) if(!acctId[t.card]||!xferPayee[acctId[t.card]]){ console.log('NO TRANSFER PAYEE for', t.card); bad++; }
if(bad){ console.log('aborting'); await api.shutdown(); process.exit(2); }
console.log(`plan ok: ${plan.sets.length} category sets, ${plan.transfers.length} transfer conversions; mode=${APPLY?'APPLY':'dry-run'}`);
if(APPLY){
  let n=0;
  for(const s of plan.sets){ await api.updateTransaction(s.id,{category:catId[s.cat]}); n++; }
  console.log('categories set:',n);
  for(const t of plan.transfers){
    await api.deleteTransaction(t.delete_id);
    await api.updateTransaction(t.house_id,{payee:xferPayee[acctId[t.card]],category:null});
    console.log('transfer:',t.date,t.amount,'->',t.card);
  }
  await api.sync();
}
// verification (works in both modes; in dry-run it reports the pre-state)
const today=new Date().toISOString().slice(0,10); let uncat=0; const pm=Object.fromEntries(payees.map(p=>[p.id,p.name]));
for(const a of accounts){ if(a.offbudget||a.closed) continue; for(const t of await api.getTransactions(a.id,'2000-01-01',today)){ if(t.is_parent||t.transfer_id) continue; if(/^starting balance$/i.test(pm[t.payee]||'')) continue; if(!t.category) uncat++; } }
console.log('uncategorized on-budget transactions now:',uncat);
for(const t of plan.transfers){ const h=(await api.getTransactions(acctId['HOUSE (21)'],t.date,t.date)).find(x=>x.id===t.house_id); console.log('  house', t.date, t.amount, 'transfer_id=', h?.transfer_id?'yes':'no', 'category=', h?.category?'SET':'none'); }
await api.shutdown();
