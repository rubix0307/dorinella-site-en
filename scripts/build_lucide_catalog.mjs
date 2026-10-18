// Builds app/core/lucide.json — every Lucide icon (inner SVG + search tags) for the icon picker of the
// /links/ page admin and for rendering the chosen icon on the page.
//
// Run:  npm i --no-save --prefix .icon-build lucide-static@1.8.0
//       node scripts/build_lucide_catalog.mjs .icon-build
import {readFileSync, writeFileSync} from 'node:fs';
import {join, resolve} from 'node:path';

const ROOT = resolve(import.meta.dirname, '..');
const pkg = join(resolve(process.argv[2] || '.icon-build'), 'node_modules', 'lucide-static');
const nodes = JSON.parse(readFileSync(join(pkg, 'icon-nodes.json'), 'utf8'));
const tags = JSON.parse(readFileSync(join(pkg, 'tags.json'), 'utf8'));

const esc = (v) => String(v).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const inner = (node) => node.map(([tag, attrs]) =>
    `<${tag} ${Object.entries(attrs).filter(([k]) => k !== 'key').map(([k, v]) => `${k}="${esc(v)}"`).join(' ')}/>`).join('');

const out = {};
for (const name of Object.keys(nodes).sort()) out[name] = [inner(nodes[name]), (tags[name] || []).join(' ')];
writeFileSync(join(ROOT, 'app/core/lucide.json'), JSON.stringify(out));
console.log(`${Object.keys(out).length} icons`);
