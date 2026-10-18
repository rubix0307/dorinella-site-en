/* The admin without page loads (Alpine.js is for the admin only).

   1. The sidebar is an Alpine component: on a page of a section it holds two menus (the pages of Telegram, the sections of the admin) and the entry
      "Telegram" / the button "Усі розділи адмінки" flip between them; nothing is loaded.
   2. Pages of the Telegram section change in place: a click on a link, a GET form (filters) or a POST form inside such a page fetches the next page, and its main part,
      the menus' marks and the title take the place of the current ones; the address and the Back button follow. A page opts in with `data-soft` on its
      <main>; a page that does not (the statistics with its charts) is loaded the usual way, and so is a link that goes wrong.
   Scripts of a page that are not loaded yet are loaded first, then `admin:page` tells the page's script that a page came; a page with unsaved work can say
   no to leaving it (`admin:leave`, cancelable; it may ask the admin first and hand its answer over as `event.detail.pending`, a Promise); a script that moves the address itself says so (`admin:address`).
   History: every entry carries its number and the scroll position in `history.state` ({i, y}); a Back or Forward that the admin refuses is undone by going the
   other way, so the address never disagrees with the page. */
(() => {
    const MAIN = 'main.admin-main';
    const SECTION = '/admin/telegram';
    const OFFLINE = 'Немає зв’язку з сервером. Дію не виконано, спробуйте ще раз.';
    let busy = null;                                   // {controller, posting} of the request in flight
    const bare = (address) => String(address).split('#')[0];
    let onScreen = bare(location.href);                // the address of the page on screen: tells a move in the history from a jump to an anchor
    let index = history.state?.i ?? 0;                 // the number of the entry on screen
    let restoring = false;                             // a move of ours (undoing a refused Back): its popstate is not a navigation
    let asking = null;                                 // the question "leave the page?" is on screen: {back} is the newest Back/Forward that wants to leave (the latest one wins, none asks again)
    history.replaceState({...history.state, i: index}, '');
    function rewind(delta) {                           // undo a move of the browser; a zero move fires no popstate, so nothing is armed for it
        if (!delta) return;
        restoring = true;
        history.go(delta);
        setTimeout(() => { restoring = false; }, 1000);    // if no popstate ever comes, the flag does not stay
    }

    document.addEventListener('alpine:init', () => {
        Alpine.store('soft', {busy: false});
        Alpine.data('adminSide', (view) => ({
            view,
            home: view,                                // the menu the page on screen starts on: a soft navigation changes `view` only when that changes
            swapped: false,                            // the menus come in with a motion only after the first flip (not when a page opens)
            open(key) {                                // 'all' or the key of a group: the menu to show
                const was = this.view;
                this.view = key;
                this.swapped = true;
                // the matching switch in the other menu: the back button of a group, or the entry of the group that was left; the keyboard stays where it was
                const target = key === 'all' ? `[data-side-view="all"] [data-side-toggle="${was}"]` : `[data-side-view="${key}"] [data-side-toggle="all"]`;
                this.$nextTick(() => this.$root.querySelector(target)?.focus({preventScroll: true}));
            },
        }));
    });
    // Alpine did not come (blocked, 404): the page is as without JS, both menus of the sidebar are open and every link works
    window.addEventListener('load', () => { if (!window.Alpine) document.documentElement.classList.replace('js', 'no-js'); });
    // admin-confirm.js did not come either: the dangerous forms must not stay locked (see `js-ready` in admin.css)
    window.addEventListener('load', () => { if (!document.documentElement.classList.contains('js-ready')) document.documentElement.classList.replace('js', 'no-js'); });
    document.addEventListener('admin:address', () => { onScreen = bare(location.href); });
    let said = '';                                     // what the next page shown says to a screen reader instead of its own title
    document.addEventListener('admin:reload', (event) => {     // the page asks for itself again (the calendar after a move): `only` is what must still be on screen, `say` what to tell
        const again = (turns) => {
            if (event.detail?.only && !document.querySelector(MAIN)?.querySelector(event.detail.only)) return;     // the admin went elsewhere meanwhile
            if (busy && turns) { setTimeout(again, 150, turns - 1); return; }       // a page is on its way (a month): not cancelled by this
            said = event.detail?.say || '';
            go(location.href, 'replace');
        };
        if (soft()) again(20);
    });

    const soft = () => document.querySelector(MAIN)?.hasAttribute('data-soft');
    const inSection = (url) => url.origin === location.origin && (url.pathname === SECTION || url.pathname.startsWith(`${SECTION}/`));
    const state = (on) => { if (window.Alpine?.store) Alpine.store('soft').busy = on; };
    const hard = (url) => location.assign(url);

    function complain(message) {                       // an action that did not reach the server: said on the page, nothing is lost
        const main = document.querySelector(MAIN);
        main?.querySelector('.soft-alert')?.remove();
        const note = Object.assign(document.createElement('p'), {className: 'admin-alert admin-alert--error soft-alert', role: 'alert', textContent: message});
        main?.prepend(note);
        note.scrollIntoView({block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    }

    async function scriptsOf(next) {                   // the scripts of the next page that this one has not loaded
        const have = new Set([...document.scripts].map((s) => s.src && new URL(s.src, location.href).pathname));
        for (const script of next.querySelectorAll('script[src]')) {
            const src = new URL(script.getAttribute('src'), location.href);
            if (have.has(src.pathname)) continue;
            await new Promise((resolve, reject) => {
                const tag = document.createElement('script');
                tag.src = src.href;
                if (script.type) tag.type = script.type;
                tag.onload = resolve;
                tag.onerror = reject;
                document.head.append(tag);
            });
            have.add(src.pathname);
        }
    }

    /* puts a page (the answer of the server, at `address`) in place of this one; false when it cannot be done in place.
       how: 'push' (a new place in the history), 'replace' (the same place), 'pop' (Back or Forward: the browser has moved already), 'keep' (the address stays);
       `where` is the scroll position to go back to (a Back), `signal` tells that a newer navigation took over while the scripts were loading */
    async function show(html, address, how, {signal, where = 0} = {}) {
        const next = new DOMParser().parseFromString(html, 'text/html');
        const fresh = next.querySelector(MAIN);
        const here = document.querySelector(MAIN);
        if (!fresh || !here || !fresh.hasAttribute('data-soft') || !inSection(new URL(address))) return false;
        try { await scriptsOf(next); } catch { return false; }
        if (signal?.aborted) return true;              // another page was asked for meanwhile: that one is shown, this one is dropped
        const active = document.activeElement;         // what has the keyboard now: found again in the new page (a month's arrow, a filter)
        const keep = active && here.contains(active) ? ['aria-label', 'id', 'name', 'href'].map((name) => [name, active.getAttribute(name)]).find(([, value]) => value) : null;
        const sideLink = active?.closest?.('#admin-side a[href]')?.getAttribute('href');      // a link of the sidebar that points at this very page is replaced with the menus: the keyboard must not fall to the body
        const hadFocus = !!active && active !== document.body;
        here.replaceChildren(...fresh.childNodes);
        here.className = fresh.className;
        document.title = next.title;
        const side = document.querySelector('#admin-side');
        const newSide = next.querySelector('#admin-side');
        if (side && newSide) {                         // the marks of the current page, and the badges
            newSide.querySelectorAll('nav[data-side-view]').forEach((menu) => {
                side.querySelector(`nav[data-side-view="${menu.dataset.sideView}"]`)?.replaceChildren(...menu.childNodes);
            });
            const data = window.Alpine?.$data ? Alpine.$data(side) : null;
            if (data && newSide.dataset.view && data.home !== newSide.dataset.view) data.home = data.view = newSide.dataset.view;
        }
        const previous = location.pathname;
        const url = new URL(address);
        url.searchParams.delete('notice');             // the notice is on the page now; a reload should not bring it back
        if (how === 'push') {
            history.replaceState({...history.state, i: index, y: scrollY}, '');         // where this entry was left, for a Back to it
            index += 1;
            history.pushState({i: index}, '', url);
        } else if (how === 'replace' || (how === 'pop' && bare(url.href) !== bare(location.href))) {
            history.replaceState({...history.state, i: index}, '', url);
        }
        onScreen = bare(location.href);
        document.dispatchEvent(new CustomEvent('admin:page'));
        const main = document.querySelector(MAIN);
        const samePage = url.pathname === previous;                        // a filter, a month: the same page with other contents: the place and the focus stay
        if (!samePage) {
            const heading = main.querySelector('h1') || main;
            heading.setAttribute('tabindex', '-1');
            heading.focus({preventScroll: true});
        }
        if (samePage && keep) main.querySelector(`[${keep[0]}="${CSS.escape(keep[1])}"]`)?.focus({preventScroll: true});
        if (sideLink) document.querySelector(`#admin-side a[href="${CSS.escape(sideLink)}"]`)?.focus({preventScroll: true});
        if (samePage && hadFocus && (!document.activeElement || document.activeElement === document.body)) {     // the element that had the keyboard is gone and has no name to look for: the heading of the page takes it
            const heading = main.querySelector('h1') || main;
            heading.setAttribute('tabindex', '-1');
            heading.focus({preventScroll: true});
        }
        const status = document.getElementById('soft-status');             // what a screen reader is told: what the page asked to say, or the month of the calendar, or the title
        if (status) {                                  // cleared first: the same text twice in a row (the same title) would be silent
            const text = said || main.querySelector('[data-announce]')?.textContent || document.title;
            status.textContent = '';
            requestAnimationFrame(() => { status.textContent = text; });
        }
        said = '';
        let anchor = null;
        try { anchor = url.hash && document.getElementById(decodeURIComponent(url.hash.slice(1))); } catch { /* a hash that is not an address: no anchor */ }
        if (anchor) anchor.scrollIntoView();
        else if (how === 'pop') scrollTo({top: where});
        else if (how !== 'keep' && !samePage) scrollTo({top: 0});
        return true;
    }

    /* fetches a page (a link, a GET form, or a POST form with `init`) and shows it; a page that cannot be shown in place is opened the usual way.
       `back` (a Back or Forward) is {to: the number of the entry, y: its scroll position} */
    async function go(address, how, init, back = {}, asked = false) {
        const posting = init?.method === 'POST';
        if (busy?.posting) {                           // an action is on its way to the server: nothing may cancel it, and it is not sent twice
            if (how === 'pop') rewind(index - back.to);          // a Back meanwhile is undone
            return;
        }
        if (asking) {                                  // the question about leaving is open: another Back only moves its target, the browser is sent back if the answer is "stay"
            if (how === 'pop') asking.back = back;
            return;
        }
        const leave = new CustomEvent('admin:leave', {cancelable: true, detail: {pending: null}});     // a handler that must ask puts its answer (a Promise of true/false) in `detail.pending`
        if (!posting && !asked && !document.dispatchEvent(leave)) {    // leaving a page with unsaved work: the admin says no, or is being asked
            const stay = (b) => { if (how === 'pop') rewind(index - b.to); };      // the browser has moved on: it is sent back
            if (leave.detail.pending) {
                const mine = asking = {back};
                leave.detail.pending.then((yes) => {
                    asking = null;
                    if (yes) go(how === 'pop' ? location.href : address, how, init, mine.back, true); else stay(mine.back);
                }, () => { asking = null; stay(mine.back); });
            } else stay(back);
            return;
        }
        if (how === 'pop') index = back.to;
        const from = bare(location.href);
        busy?.controller.abort();
        const mine = busy = {controller: new AbortController(), posting};
        state(true);
        let answered = address;                        // where the usual way goes: after a POST the page the server sent us to (never the form again)
        try {
            const reply = await fetch(address, {credentials: 'same-origin', signal: mine.controller.signal, headers: {Accept: 'text/html'}, ...init});
            answered = reply.url;
            const html = (reply.headers.get('content-type') || '').includes('text/html');
            if (!html && !posting) reply.body?.cancel().catch(() => {});      // a file (a download, an image): its body is not read here, the usual way fetches it once
            const place = new URL(reply.url);
            place.searchParams.delete('notice');
            place.hash = new URL(address, location.href).hash;
            // a page the server sent us to is a new place (or this one again); what a POST answers without sending us anywhere (an error page) leaves the address alone
            const mode = how === 'pop' ? 'pop' : posting && !reply.redirected ? 'keep' : bare(place.href) === from ? 'replace' : 'push';
            if (html && await show(await reply.text(), place.href, mode, {signal: mine.controller.signal, where: back.y})) return;
            if (posting && !reply.redirected) complain(`Сервер відповів ${reply.status}. Дію не виконано.`);
            else hard(answered);
        } catch (error) {
            if (error.name === 'AbortError') return;
            if (posting) complain(OFFLINE);            // the form is not sent again by loading its address: that would be a GET of a POST route
            else hard(address);
        } finally {
            if (busy === mine) { busy = null; state(false); }
        }
    }

    document.addEventListener('click', (event) => {
        if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || !soft()) return;
        const link = event.target.closest('a[href]');
        if (!link || link.target || link.hasAttribute('download') || link.hasAttribute('data-hard') || !link.closest(`${MAIN}, #admin-side`)) return;
        const url = new URL(link.href, location.href);
        if (!inSection(url) || /\/media\/\d+$/.test(url.pathname)) return;       // a file of the section (the media of a post) is a download, not a page
        if (url.pathname === location.pathname && url.search === location.search) {
            if (!url.hash) event.preventDefault();      // the page the admin is on: nothing to fetch (and no reload that would skip the question about unsaved text)
            return;                                     // an anchor of this page is the browser's own business
        }
        event.preventDefault();
        go(url.href, 'push');
    });

    document.addEventListener('submit', (event) => {
        const form = event.target, by = event.submitter;
        if (event.defaultPrevented || !soft() || !form.closest(MAIN) || form.target || by?.formTarget || form.hasAttribute('data-hard')) return;
        const method = (by?.getAttribute('formmethod') || form.getAttribute('method') || 'get').toUpperCase();     // a button may have its own address and method
        const action = new URL(by?.getAttribute('formaction') || form.getAttribute('action') || location.href, location.href);
        if (!inSection(action) || (method !== 'GET' && method !== 'POST')) return;
        event.preventDefault();
        if (busy?.posting) return;                      // a second click on the same button while the first is on its way
        const data = new FormData(form, by);
        if (method === 'GET') {
            const target = new URL(action);
            target.search = new URLSearchParams([...data].filter(([, value]) => typeof value === 'string')).toString();
            go(target.href, 'push');
        } else {
            by?.setAttribute('aria-busy', 'true');
            go(action.href, 'push', {method: 'POST', body: data}).finally(() => by?.removeAttribute('aria-busy'));
        }
    });

    let saving = 0;                                    // where the page is scrolled is kept in the entry on screen: an entry left by a Back has no other chance to say it
    addEventListener('scroll', () => {
        clearTimeout(saving);
        saving = setTimeout(() => { if (!restoring && !busy) history.replaceState({...history.state, i: index, y: scrollY}, ''); }, 120);
    }, {passive: true});

    window.addEventListener('popstate', (event) => {
        if (restoring) { restoring = false; return; }
        if (asking) { asking.back = {to: event.state?.i ?? index, y: event.state?.y ?? 0}; return; }
        if (bare(location.href) === onScreen) return;   // only the anchor changed
        go(location.href, 'pop', undefined, {to: event.state?.i ?? index, y: event.state?.y ?? 0});
    });
})();
