// A searchable picker in place of a plain <select data-link-select> of short links: the select stays
// (hidden) and is what the form sends; search matches the short address, the title and the target URL.
// «New link» at the bottom makes a new analytics short link right here and picks it.
(() => {
    const t = (text, values) => window.t(text, values);
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
    const PLUS = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14"/><path d="M12 5v14"/></svg>';
    const LOUPE = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m21 21-4.34-4.34"/><circle cx="11" cy="11" r="8"/></svg>';
    let open = null;     // the state of the open picker

    function label(select) {
        const o = select.selectedOptions[0];
        if (!o || !o.value) return `<span class="link-pick__empty">${esc(o?.textContent.trim() || t('Pick a link'))}</span>`;
        return `<span class="link-pick__slug">${esc(o.dataset.slug)}</span><span class="link-pick__target">${esc(o.dataset.target)}</span>`;
    }

    function render(state) {
        const q = state.input.value.trim().toLowerCase();
        const items = [...state.select.options].filter((o) => !q || (o.value && `${o.dataset.slug} ${o.dataset.title} ${o.dataset.target}`.toLowerCase().includes(q)));
        state.list.innerHTML = items.length ? items.map((o) => `
            <li role="option" data-id="${o.value}" aria-selected="${o.selected}"${o.selected ? ' class="is-current"' : ''}>
                ${o.value ? `<span class="link-pick__slug">${esc(o.dataset.slug)}${o.dataset.off !== undefined ? ` <em>${t('off')}</em>` : ''}</span>
                ${o.dataset.title ? `<span class="link-pick__title">${esc(o.dataset.title)}</span>` : ''}
                <span class="link-pick__target">${esc(o.dataset.target)}</span>` : `<span class="link-pick__empty">${esc(o.textContent.trim())}</span>`}
            </li>`).join('') : `<li class="link-pick__none">${t('Nothing found')}</li>`;
        const current = items.findIndex((o) => o.selected);
        state.active = q || current < 0 ? 0 : current;
        mark(state);
    }

    function mark(state) {
        const rows = [...state.list.querySelectorAll('[data-id]')];
        rows.forEach((r, i) => r.classList.toggle('is-active', i === state.active));
        rows[state.active]?.scrollIntoView({block: 'nearest'});
    }

    function close() {
        if (!open) return;
        open.showForm(false);
        open.box.classList.remove('is-open');
        open.pop.hidden = true;
        open.button.setAttribute('aria-expanded', 'false');
        open = null;
    }

    function choose(state, id) {
        state.select.value = id;
        state.select.dispatchEvent(new Event('change', {bubbles: true}));
        state.button.innerHTML = label(state.select);
        close();
        state.button.focus();
    }

    function enhance(select) {
        if (select.dataset.enhanced) return;
        select.dataset.enhanced = '1';
        const box = document.createElement('div');
        box.className = 'link-pick';
        box.innerHTML = `<button type="button" class="link-pick__button" aria-haspopup="listbox" aria-expanded="false">${label(select)}</button>
            <div class="link-pick__pop" hidden>
                <label class="link-pick__search">${LOUPE}<input type="search" placeholder="${t('Short link or address…')}" autocomplete="off"></label>
                <ul class="link-pick__list" role="listbox"></ul>
                <button type="button" class="link-pick__add" data-add>${PLUS}${t('New link')}</button>
                <div class="link-pick__new" hidden data-new>
                    <p class="link-pick__new-title">${t('New link for the analytics')}</p>
                    <label>${t('Where it leads')}<input type="url" data-f="target_url" placeholder="https://…" autocomplete="off"></label>
                    <label>${t('Code')} <small>${t('optional, made for you if empty')}</small><input data-f="slug" placeholder="${t('e.g. links-shop')}" autocomplete="off" maxlength="32"></label>
                    <label>${t('Title')} <small>${t('how it appears in the analytics')}</small><input data-f="title" placeholder="${t('e.g. Link · Shop')}" autocomplete="off" maxlength="200"></label>
                    <p class="link-pick__error" role="alert" hidden data-error></p>
                    <div class="link-pick__new-actions">
                        <button type="button" class="btn btn--ghost btn--sm" data-cancel>${t('Cancel')}</button>
                        <button type="button" class="btn btn--primary btn--sm" data-create>${PLUS}${t('Create')}</button>
                    </div>
                </div>
            </div>`;
        select.hidden = true;
        select.required = false;          // a hidden control cannot show the browser's message; the server checks it
        select.after(box);
        const q = (sel) => box.querySelector(sel);
        const state = {select, box, button: q('.link-pick__button'), pop: q('.link-pick__pop'), input: q('input[type=search]'),
                       list: q('ul'), active: 0};
        const form = q('[data-new]'), errorBox = q('[data-error]'), addButton = q('[data-add]');
        const field = (key) => form.querySelector(`[data-f="${key}"]`);

        state.showForm = (on) => {
            form.hidden = !on;
            box.classList.toggle('is-creating', on);
            addButton.hidden = state.list.hidden = state.input.closest('label').hidden = on;
            errorBox.hidden = true;
        };

        state.button.addEventListener('click', () => {
            if (open === state) return close();
            close();
            open = state;
            box.classList.add('is-open');
            state.pop.hidden = false;
            state.button.setAttribute('aria-expanded', 'true');
            state.input.value = '';
            render(state);
            state.input.focus();
        });
        state.input.addEventListener('input', () => render(state));
        state.input.addEventListener('keydown', (e) => {
            const rows = state.list.querySelectorAll('[data-id]');
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                state.active = Math.max(0, Math.min(rows.length - 1, state.active + (e.key === 'ArrowDown' ? 1 : -1)));
                mark(state);
            } else if (e.key === 'Enter') {
                e.preventDefault();
                if (rows[state.active]) choose(state, rows[state.active].dataset.id);
            } else if (e.key === 'Escape') {
                close();
                state.button.focus();
            }
        });
        state.list.addEventListener('click', (e) => {
            const row = e.target.closest('[data-id]');
            if (row) choose(state, row.dataset.id);
        });

        addButton.addEventListener('click', () => {
            const searched = state.input.value.trim();
            form.querySelectorAll('input').forEach((i) => { i.value = ''; });
            if (/^https?:\/\//.test(searched)) field('target_url').value = searched;   // what was searched for is likely the address
            state.showForm(true);
            field('target_url').focus();
        });
        q('[data-cancel]').addEventListener('click', () => { state.showForm(false); state.input.focus(); });
        form.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); q('[data-create]').click(); }
            if (e.key === 'Escape') { e.preventDefault(); state.showForm(false); state.input.focus(); }
        });
        // a link to the same address already exists: pick it, or make another one anyway (e.g. another source)
        function offerDuplicate(message, same, create) {
            errorBox.innerHTML = `<span>${esc(message)}</span><span class="link-pick__dup-actions">
                <button type="button" class="btn btn--primary btn--sm" data-use>${esc(t('Pick %(slug)s', {slug: same.slug}))}</button>
                <button type="button" class="btn btn--ghost btn--sm" data-anyway>${t('Create anyway')}</button></span>`;
            errorBox.hidden = false;
            errorBox.querySelector('[data-use]').addEventListener('click', () => {
                if (!select.querySelector(`option[value="${same.id}"]`)) addOption(same);
                choose(state, String(same.id));
            });
            errorBox.querySelector('[data-anyway]').addEventListener('click', (e) => create(true, e.currentTarget));
        }
        function addOption(link) {
            for (const s of document.querySelectorAll('select[data-link-select]')) {
                if (s.querySelector(`option[value="${link.id}"]`)) continue;
                const o = new Option(link.slug + (link.title ? ' · ' + link.title : ''), link.id);
                Object.assign(o.dataset, {slug: link.slug, target: link.target_url, title: link.title || ''});
                s.append(o);
            }
        }
        const create = async (allowDuplicate = false, button = q('[data-create]')) => {
            button.disabled = true;
            button.classList.add('is-loading');
            if (!errorBox.contains(button)) errorBox.hidden = true;     // «Create anyway» spins where it is
            try {
                const response = await fetch(select.dataset.createUrl, {
                    method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({target_url: field('target_url').value, slug: field('slug').value, title: field('title').value,
                                          allow_duplicate: allowDuplicate}),
                });
                const link = await response.json().catch(() => ({}));
                if (response.status === 409 && link.duplicate) return offerDuplicate(link.error, link.duplicate, create);
                if (response.status === 403 && !link.error) throw new Error(t('You do not have the rights to create links.'));
                if (!response.ok) throw new Error(link.error || t('Could not create the link.'));
                addOption(link);              // every picker on the page gets the new link; this one picks it
                choose(state, String(link.id));
            } catch (err) {
                errorBox.textContent = err.message;
                errorBox.hidden = false;
            } finally {
                button.disabled = false;
                button.classList.remove('is-loading');
            }
        };
        q('[data-create]').addEventListener('click', () => create());
    }

    // also after the form is saved and its parts are replaced (admin-links-page-save.js)
    window.enhanceLinkSelects = () => document.querySelectorAll('select[data-link-select]').forEach(enhance);
    window.enhanceLinkSelects();
    document.addEventListener('pointerdown', (e) => { if (open && !open.box.contains(e.target)) close(); });
})();
