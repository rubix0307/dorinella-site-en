// The /links/ page admin: the icon panel (like Telegram's reaction picker) for each button.
// A logo, any Lucide icon or a photo (shrunk here to a 192 px square webp before it is sent).
// Icons come from /admin/links/icons.json on first open, in groups as on lucide.dev. Only what is in
// view (and a little below) is drawn: each group is cut into blocks of a few rows, empty and sized
// until an IntersectionObserver sees them coming; search results are drawn a page at a time.
(() => {
    const panel = document.querySelector('[data-icon-panel]');
    if (!panel) return;
    const search = panel.querySelector('[data-icon-search]');
    const scroller = panel.querySelector('.emoji-panel__scroll');
    const groupsBox = panel.querySelector('[data-icon-groups]');
    const results = panel.querySelector('[data-icon-results]');
    const tabs = panel.querySelector('[data-icon-tabs]');
    const top = panel.querySelector('[data-section="top"]');
    const empty = panel.querySelector('[data-icon-empty]');
    const CELL = 44, ROWS = 4, PAGE = 64;        // px per icon cell (42 + 2 gap), rows per block, results per page
    const SVG = (inner) => `<svg class="icon" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
    let row = null;          // the [data-icon-pick] being edited
    let data = null;         // {icons: name → [inner svg, tags], groups: [{key, label, tab, icons}]}
    let found = [];          // search matches not drawn yet
    const current = () => row?.querySelector('[data-image]').value;
    const ukName = (name) => data.uk?.[name]?.[0] || name;
    const button = (name) => `<button type="button" data-value="${name}" data-tip="${ukName(name)}" aria-label="${ukName(name)}"${name === current() ? ' class="is-current"' : ''}>${SVG(data.icons[name][0])}</button>`;

    // blocks are drawn when they come within 300 px of the visible part of the panel
    const drawer = new IntersectionObserver((entries) => {
        for (const {isIntersecting, target} of entries) {
            if (!isIntersecting) continue;
            drawer.unobserve(target);
            if (target.dataset.sentinel !== undefined) { drawResults(); continue; }
            target.innerHTML = target.dataset.names.split(' ').map(button).join('');
            target.style.height = '';
        }
    }, {root: scroller, rootMargin: '300px 0px'});

    function columns() {
        return Math.max(1, Math.floor((scroller.clientWidth - 12 + 2) / CELL));
    }

    async function load() {
        if (data) return;
        data = await (await fetch(panel.dataset.iconsUrl, {credentials: 'same-origin'})).json();
        // one lowercase line per icon to search in: English name and tags, Ukrainian name and words
        data.index = Object.fromEntries(Object.keys(data.icons).map((name) => [name,
            [name.replace(/-/g, ' '), data.icons[name][1], ...(data.uk?.[name] || [])].join(' ').toLowerCase()]));
        const per = columns() * ROWS;
        groupsBox.innerHTML = data.groups.map((g) => {
            const blocks = [];
            for (let i = 0; i < g.icons.length; i += per) {
                const names = g.icons.slice(i, i + per);
                blocks.push(`<div class="emoji-panel__grid emoji-panel__block" data-names="${names.join(' ')}" style="height:${Math.ceil(names.length / columns()) * CELL - 2}px"></div>`);
            }
            return `<section data-group="${g.key}"><p class="emoji-panel__label">${g.label}</p>${blocks.join('')}</section>`;
        }).join('');
        tabs.innerHTML = data.groups.map((g) => `<button type="button" data-tab="${g.key}" data-tip="${g.label}" aria-label="${g.label}">${SVG(g.tab)}</button>`).join('');
        groupsBox.querySelectorAll('[data-names]').forEach((b) => drawer.observe(b));
        roomForLast();
        markTab();
        filter();
    }

    function drawResults() {
        const sentinel = results.querySelector('[data-sentinel]');
        sentinel?.remove();
        results.insertAdjacentHTML('beforeend', found.splice(0, PAGE).map(button).join(''));
        if (found.length) {
            results.insertAdjacentHTML('beforeend', '<span data-sentinel class="emoji-panel__sentinel"></span>');
            drawer.observe(results.querySelector('[data-sentinel]'));
        }
    }

    function filter() {
        const q = search.value.trim().toLowerCase();
        top.hidden = groupsBox.hidden = tabs.hidden = !!q;
        results.hidden = !q;
        results.innerHTML = '';
        empty.hidden = true;
        if (!q || !data) return;
        const words = q.split(/s+/);
        found = Object.keys(data.icons).filter((name) => words.every((w) => data.index[name].includes(w)));
        empty.hidden = found.length > 0;
        scroller.scrollTop = 0;
        drawResults();
    }

    // room under the last group, so that every group — the short last ones too — can scroll up to the top
    function roomForLast() {
        const last = groupsBox.lastElementChild;
        if (!last) return;
        // exactly enough: at the very bottom the last group's title sits at the top, not cut by the edge
        groupsBox.style.paddingBottom = '0px';
        const need = last.offsetTop - scroller.offsetTop - 6;       // scrollTop that puts it at the top, with a little air
        const max = scroller.scrollHeight - scroller.clientHeight;
        groupsBox.style.paddingBottom = Math.max(0, need - max) + 'px';
    }

    // a safety net for the observer: any undrawn block in (or near) view is drawn on scroll
    function drawNear() {
        const top = scroller.scrollTop - 300, bottom = scroller.scrollTop + scroller.clientHeight + 300;
        for (const b of groupsBox.querySelectorAll('[data-names]:empty')) {
            const y = b.offsetTop - scroller.offsetTop;
            if (y < bottom && y + b.offsetHeight > top) {
                drawer.unobserve(b);
                b.innerHTML = b.dataset.names.split(' ').map(button).join('');
                b.style.height = '';
            }
        }
    }

    function markTab() {
        let active = null;
        for (const s of groupsBox.children) if (s.offsetTop - scroller.offsetTop <= scroller.scrollTop + 40) active = s.dataset.group;
        if (active === tabs.dataset.active) return;
        tabs.dataset.active = active || '';
        let current = null;
        for (const t of tabs.children) {
            t.classList.toggle('is-active', t.dataset.tab === active);
            if (t.dataset.tab === active) current = t;
        }
        // the strip follows the list: the current group's tab slides to the middle (or the start, above the groups)
        const left = current ? current.offsetLeft - (tabs.clientWidth - current.offsetWidth) / 2 : 0;
        tabs.scrollTo({left: Math.max(0, left), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    }

    function place(opener) {
        panel.hidden = false;
        if (matchMedia('(max-width: 640px)').matches) {     // phones: a sheet at the bottom
            panel.style.left = panel.style.top = '';
            return;
        }
        const r = opener.getBoundingClientRect();
        const w = panel.offsetWidth, h = panel.offsetHeight;
        const header = document.querySelector('[data-header]')?.getBoundingClientRect().bottom || 0;
        const left = Math.min(Math.max(8, r.left), innerWidth - w - 8);
        const below = r.bottom + 8 + h < innerHeight;
        panel.style.left = `${left + scrollX}px`;
        panel.style.top = `${(below ? r.bottom + 8 : Math.max(header + 8, r.top - h - 8)) + scrollY}px`;
    }

    function open(pick) {
        row = pick;
        search.value = '';
        scroller.scrollTop = 0;
        filter();
        place(pick.querySelector('[data-icon-open]'));
        if (data) roomForLast();
        panel.querySelector('[data-clear]').hidden = !pick.hasAttribute('data-optional');     // a sub-link may have no icon
        for (const b of panel.querySelectorAll('[data-value]')) b.classList.toggle('is-current', b.dataset.value === current());
        if (!matchMedia('(pointer: coarse)').matches) search.focus();
        load();
    }

    function close() {
        panel.hidden = true;
        row?.querySelector('[data-icon-open]').focus();
        row = null;
    }

    function choose(value, html) {
        row.querySelector('[data-image]').value = value;
        const photo = row.querySelector('[data-photo]');
        if (photo) photo.value = '';                           // a logo or an icon: no photo to send
        row.querySelector('[data-icon-open]').innerHTML = value ? html : SVG('<path d="M5 12h14"/><path d="M12 5v14"/>').replace('class="icon"', 'class="icon icon-pick__none"');
        close();
    }

    async function shrink(file) {
        const bitmap = await createImageBitmap(file);
        const side = Math.min(bitmap.width, bitmap.height), size = 192;
        const canvas = Object.assign(document.createElement('canvas'), {width: size, height: size});
        canvas.getContext('2d').drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, size, size);
        const blob = await new Promise((done) => canvas.toBlob(done, 'image/webp', 0.86));
        return new File([blob], 'photo.webp', {type: 'image/webp'});
    }

    document.addEventListener('click', (e) => {
        const opener = e.target.closest('[data-icon-open]');
        if (opener) {
            const pick = opener.closest('[data-icon-pick]');
            pick === row && !panel.hidden ? close() : open(pick);
            return;
        }
        if (!row) return;
        const tab = e.target.closest('[data-tab]');
        const item = e.target.closest('[data-value]');
        if (tab) {
            const section = groupsBox.querySelector(`[data-group="${tab.dataset.tab}"]`);
            scroller.scrollTo({top: section.offsetTop - scroller.offsetTop - 6, behavior: 'smooth'});
        } else if (item && panel.contains(item)) {
            choose(item.dataset.value, item.innerHTML);
        } else if (e.target.closest('[data-photo-pick]')) {
            row.querySelector('[data-photo]').click();
        } else if (!panel.contains(e.target)) {
            close();
        }
    });

    document.addEventListener('change', async (e) => {
        const input = e.target.closest('[data-photo]');
        if (!input?.files.length) return;
        const pick = input.closest('[data-icon-pick]');
        const opener = pick.querySelector('[data-icon-open]');
        opener.classList.add('is-loading');
        try {
            const file = await shrink(input.files[0]);
            const transfer = new DataTransfer();
            transfer.items.add(file);
            input.files = transfer.files;
            pick.querySelector('[data-image]').value = 'photo';
            pick.querySelector('[data-icon-open]').innerHTML = `<img class="is-photo" src="${URL.createObjectURL(file)}" alt="">`;
        } catch {
            input.value = '';
            window.adminConfirm({title: 'Фото не відкрито', message: 'Не вдалося відкрити це фото. Спробуйте PNG або JPEG.', confirmLabel: 'Зрозуміло', cancelLabel: 'Закрити'});
        }
        opener.classList.remove('is-loading');
        if (row) close();
    });

    // the tooltip: our own, shown after a short pause over an icon (or on keyboard focus)
    const tip = Object.assign(document.createElement('div'), {className: 'emoji-tip', role: 'tooltip', hidden: true});
    panel.append(tip);
    let tipTimer = 0;
    function showTip(el) {
        clearTimeout(tipTimer);
        tipTimer = setTimeout(() => {
            const name = el.dataset.value;
            tip.innerHTML = '';
            tip.append(Object.assign(document.createElement('strong'), {textContent: el.dataset.tip}));
            if (name && el.dataset.tip !== name && !el.hasAttribute('data-logo')) {   // the English name under it
                tip.append(Object.assign(document.createElement('small'), {textContent: name}));
            }
            tip.hidden = false;
            const p = panel.getBoundingClientRect(), r = el.getBoundingClientRect();
            const x = Math.min(Math.max(r.left + r.width / 2 - p.left, tip.offsetWidth / 2 + 6), p.width - tip.offsetWidth / 2 - 6);
            const above = r.top - p.top > tip.offsetHeight + 14;
            tip.style.left = x + 'px';
            tip.style.top = (above ? r.top - p.top - tip.offsetHeight - 8 : r.bottom - p.top + 8) + 'px';
            tip.classList.toggle('is-below', !above);
            tip.style.setProperty('--arrow', (r.left + r.width / 2 - p.left - x) + 'px');
        }, tip.hidden ? 280 : 40);
    }
    function hideTip() { clearTimeout(tipTimer); tip.hidden = true; }
    panel.addEventListener('pointerover', (e) => {
        const el = e.target.closest('[data-tip]');
        el && e.pointerType !== 'touch' ? showTip(el) : hideTip();
    });
    panel.addEventListener('pointerleave', hideTip);
    panel.addEventListener('focusin', (e) => { const el = e.target.closest('[data-tip]'); el ? showTip(el) : hideTip(); });
    scroller.addEventListener('scroll', hideTip, {passive: true});

    search.addEventListener('input', filter);
    scroller.addEventListener('scroll', () => { markTab(); drawNear(); }, {passive: true});
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && row) close(); });
    addEventListener('resize', () => { if (row) { place(row.querySelector('[data-icon-open]')); roomForLast(); } });
})();
