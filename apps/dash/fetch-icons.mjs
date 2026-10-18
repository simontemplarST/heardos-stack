// fetch-icons.mjs — image build step: download every app icon named in apps.json from dashboard-icons into
// public/icons, so the desktop never loads third-party images at run time. A missing icon is only a warning.
import fs from "node:fs";
const cat = JSON.parse(fs.readFileSync(new URL("./apps.json", import.meta.url)));
const names = [...new Set(cat.apps.map((a) => a.icon).filter((i) => !i.startsWith("builtin:")))];
let ok = 0;
for (const n of names) {
  const r = await fetch(`https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/${n}.png`);
  if (!r.ok) { console.warn(`icon ${n}: HTTP ${r.status} — that app shows without an icon`); continue; }
  fs.writeFileSync(new URL(`./public/icons/${n}.png`, import.meta.url), Buffer.from(await r.arrayBuffer())); ok++;
}
console.log(`icons: ${ok}/${names.length}`);
