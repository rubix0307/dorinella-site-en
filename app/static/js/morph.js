// Icons that change state morph in place: morphicons (https://www.morphicons.com) on Lucide data.
// Markup comes from the micon() template helper: <svg data-micon="copy"><path data-morph d="…"/></svg>,
// identical to the static icon, so if this module never loads the page simply keeps static icons.
//
//   window.morphIcon(el, 'check')            — morph the icon in/under `el` (any script, any time)
//   <svg data-micon="ellipsis" data-micon-open="x">  inside a [popovertarget] button or a <summary>:
//                                               morphs automatically while the popover / details is open
import {createMorph} from '../vendor/morphicons/dom.js';
import ICONS from './morph-icons.js';

const OPTIONS = {reducedMotion: 'user'};
const morphs = new WeakMap();

const svgOf = (el) => (el?.matches?.('svg[data-micon]') ? el : el?.querySelector?.('svg[data-micon]'));

export function morphIcon(el, name, spring = 'snappy') {
    const svg = svgOf(el);
    const path = svg?.querySelector('[data-morph]');
    if (!path || !ICONS[name] || svg.dataset.micon === name) return;
    let morph = morphs.get(path);
    if (!morph) {
        morph = createMorph(path, ICONS[svg.dataset.micon] ?? path.getAttribute('d'), OPTIONS);
        morphs.set(path, morph);
    }
    svg.dataset.micon = name;
    morph.morphTo(ICONS[name], spring);
}
window.morphIcon = morphIcon;

// open/closed pairs: popovers (filters, menus, sort) and <details> (collapsible blocks)
const pair = (svg, open) => {
    svg.dataset.miconClosed ??= svg.dataset.micon;
    morphIcon(svg, open ? svg.dataset.miconOpen : svg.dataset.miconClosed);
};
document.addEventListener('toggle', (e) => {
    const target = e.target;
    if (!(target instanceof Element)) return;
    const open = e.newState ? e.newState === 'open' : target.open;
    if (target.hasAttribute('popover')) {
        document.querySelectorAll(`[popovertarget="${CSS.escape(target.id)}"] svg[data-micon-open]`).forEach((svg) => pair(svg, open));
    } else if (target.matches('details')) {
        target.querySelectorAll(':scope > summary svg[data-micon-open]').forEach((svg) => pair(svg, open));
    }
}, true);

// Burger ↔ close (main.js announces the menu state)
document.addEventListener('menu:change', (e) => {
    const svg = document.querySelector('[data-menu-toggle] svg[data-micon]');
    if (svg) morphIcon(svg, e.detail.open ? 'x' : 'menu');
});

// Accordion plus ↔ minus, started together with the height animation (main.js)
document.addEventListener('accordion:change', (e) => {
    const svg = e.target.querySelector?.(':scope > summary svg[data-micon]');
    if (svg) morphIcon(svg, e.detail.open ? 'minus' : 'plus', 'smooth');
}, true);

document.dispatchEvent(new CustomEvent('morph:ready'));
