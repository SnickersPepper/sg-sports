#!/usr/bin/env node
// Coverage audit: which canonical identities have a bundled crest and curated colors, per league, plus aliases that are
// ambiguous inside their scope (those intentionally resolve to nothing). Run: node scripts/audit-team-brands.mjs
import fs from "node:fs";
const assets = new URL("../android/app/src/main/assets/", import.meta.url);
// Crest images are not part of the public tree; the maintainer's tree keeps them in src/private/assets-art (see docs/LICENSING-AUDIT.md).
const art = new URL("../android/app/src/private/assets-art/", import.meta.url);
const crestUrl = (file) => [art, assets].map((base) => new URL(file, base)).find((u) => fs.existsSync(u));
const ids = JSON.parse(fs.readFileSync(new URL("team-identities.json", assets), "utf8"));
const colors = JSON.parse(fs.readFileSync(new URL("team-brand-colors.json", assets), "utf8")).teams;
const has = new Set(colors.map(t => `${t.league}|${t.sport}|${t.name}`));
const origin = new Map(colors.filter(t => t.origin).map(t => [`${t.league}|${t.sport}|${t.name}`, t.origin]));
const rows = new Map();
for (const i of ids) {
  const r = rows.get(i.league) ?? { total: 0, crest: 0, color: 0, gaps: [] };
  r.total++;
  if (i.file && crestUrl(i.file)) r.crest++;
  if (has.has(`${i.league}|${i.sport}|${i.name}`)) { r.color++; if (origin.get(`${i.league}|${i.sport}|${i.name}`)) r.derived = (r.derived ?? 0) + 1; } else r.gaps.push(i.name);
  rows.set(i.league, r);
}
let T = 0, C = 0, K = 0;
console.log("league | identities | crest file present | colors (of which crest-derived) | no color");
for (const [l, r] of rows) { T += r.total; C += r.crest; K += r.color; console.log(`${l} | ${r.total} | ${r.crest} | ${r.color} (${r.derived ?? 0} crest-derived) | ${r.total - r.color}`); }
console.log(`TOTAL | ${T} | ${C} | ${K} | ${T - K}`);
const key = v => v.normalize("NFKD").toLowerCase().replace(/[^\x00-\x7f]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
const amb = new Map();
for (const i of ids) for (const a of new Set([...i.aliases, i.name].map(key))) {
  const k = `${i.sport === "soccer" ? "soccer" : i.league}|${a}`; (amb.get(k) ?? amb.set(k, new Set()).get(k)).add(i.file || i.name);
}
const ambiguous = [...amb].filter(([, s]) => s.size > 1);
console.log(`ambiguous alias keys in scope (resolve to no identity): ${ambiguous.length}`);
if (process.argv.includes("--gaps")) for (const [l, r] of rows) console.log(`\n${l} gaps (${r.gaps.length}): ${r.gaps.join("; ")}`);
