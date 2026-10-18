// «Gut Massage»: small progressive enhancements of the course pages. Every page reads fully without this script.
// Answers (the safety confirmation, ticked lists, the chosen goal) and the progress live in the database: the page arrives with them
// (#abm-state) and every change is sent to the server, which keeps it with the time. Nothing about the course is kept in the browser.
(() => {
    const root = document.querySelector('[data-abm]');
    if (!root) return;
    const page = root.dataset.page;
    const $$ = (sel, ctx = root) => Array.from(ctx.querySelectorAll(sel));

    let initial = {};
    try { initial = JSON.parse(document.getElementById('abm-state').textContent) || {}; } catch (e) { /* no state */ }
    const saveUrl = root.dataset.save;
    const saved = (key, fallback = null) => (initial[key] && initial[key].value !== undefined ? initial[key].value : fallback);

    // one request at a time per key keeps the last answer last
    const chain = {};
    const save = (key, value) => {
        const run = () => fetch(saveUrl, {
            method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, value }),
        }).then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
            .then((j) => {
                if (j.saved) initial[key] = { value };
                return j;
            })
            .catch(() => { /* nothing to show: the page works the same, the answer is simply not kept */ });
        chain[key] = (chain[key] || Promise.resolve()).then(run);
        return chain[key];
    };
    window.abmSave = save;

    // ---------- safety page: stop panel / caution, the ticked items and the confirmation (kept with the time) ----------
    const safety = root.querySelector('[data-safety]');
    const ackOk = () => { const v = saved('safety'); return !!(v && v.ack); };
    if (safety) {
        const stop = safety.querySelector('[data-stop]');
        const caution = safety.querySelector('[data-caution]');
        const abs = $$('[data-abs]', safety), rel = $$('[data-rel]', safety);
        const ack = safety.querySelector('[data-ack]');
        const need = document.getElementById('abm-need');
        let ready = false;
        const paintAck = () => {
            if (need && ack.checked) need.hidden = true;
        };
        const sync = () => {
            const was = stop.classList.contains('is-active');
            stop.classList.toggle('is-active', abs.some((c) => c.checked));
            if (!was && stop.classList.contains('is-active') && ready) stop.scrollIntoView({ block: 'center', behavior: 'smooth' });
            caution.hidden = !rel.some((c) => c.checked);
        };
        const ticked = new Set(saved('checks', []));
        [...abs, ...rel].forEach((c) => {
            c.checked = ticked.has(c.dataset.abs || c.dataset.rel);
            c.addEventListener('change', () => {
                sync();
                const ids = [...abs, ...rel].filter((x) => x.checked).map((x) => x.dataset.abs || x.dataset.rel);
                save('checks', ids);
            });
        });
        ack.checked = ackOk();
        // once confirmed it stays confirmed
        const lock = () => { if (ack.checked) { ack.disabled = true; ack.closest('.abm-check').classList.add('is-locked'); } };
        paintAck(); lock();
        ack.addEventListener('change', () => {
            if (!ack.checked) { ack.checked = true; return; }
            save('safety', { ack: true }).then(paintAck); paintAck(); lock();
            box.classList.remove('is-need');
        });
        // «Next» without the confirmation: point at it instead of leaving
        const box = safety.querySelector('[data-ack-box]');
        const next = document.querySelector('[data-abm-next]');
        const demand = () => {
            if (need) need.hidden = false;
            box.classList.remove('is-need'); void box.offsetWidth; box.classList.add('is-need');
            box.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
            ack.focus({ preventScroll: true });
        };
        if (next) next.addEventListener('click', (e) => { if (!ack.checked) { e.preventDefault(); demand(); } });
        if (box.classList.contains('is-need') && !ack.checked) setTimeout(demand, 50);
        sync();
        ready = true;
    }

    // ---------- <details> that open and close smoothly; the questions page keeps one answer open ----------
    const calm = matchMedia('(prefers-reduced-motion: reduce)');
    const slide = (d, open) => {
        const summary = d.querySelector('summary');
        if (d._anim) d._anim.cancel();
        const from = d.offsetHeight;
        if (open) d.open = true;
        const to = open ? d.offsetHeight : summary.offsetHeight;
        if (calm.matches || from === to) { d.open = open; d.classList.remove('is-closing'); return; }
        d.classList.toggle('is-closing', !open);
        d.style.overflow = 'hidden';
        const anim = d.animate([{ height: from + 'px' }, { height: to + 'px' }], { duration: 260, easing: 'cubic-bezier(.2,.7,.2,1)' });
        d._anim = anim;
        anim.onfinish = anim.oncancel = () => {
            if (d._anim !== anim) return;
            d._anim = null; d.open = open; d.style.overflow = ''; d.classList.remove('is-closing');
        };
    };
    const smooth = (list, exclusive) => list.forEach((d) => {
        d.querySelector('summary').addEventListener('click', (e) => {
            e.preventDefault();
            const open = !d.open || d.classList.contains('is-closing');
            if (open && exclusive) list.forEach((o) => { if (o !== d && o.open) slide(o, false); });
            slide(d, open);
        });
    });
    smooth($$('.abm-q'), true);
    smooth($$('.abm-stoplist'), false);

    // ---------- the start page: a big sticky «Continue» whenever the one at the top is out of sight ----------
    const ctaTop = root.querySelector('[data-cta-top]'), ctaSticky = root.querySelector('[data-cta-sticky]');
    if (ctaTop && ctaSticky && 'IntersectionObserver' in window) {
        const sticky = ctaSticky.querySelector('a');
        new IntersectionObserver(([entry]) => {
            const away = !entry.isIntersecting;
            if (away) ctaSticky.hidden = false;
            requestAnimationFrame(() => ctaSticky.classList.toggle('is-on', away));
            sticky.tabIndex = away ? 0 : -1;
            if (!away) setTimeout(() => { if (!ctaSticky.classList.contains('is-on')) ctaSticky.hidden = true; }, 300);
        }, { threshold: 0 }).observe(ctaTop);
    }

    // ---------- anatomy: hover or tap an area or a point, or pick a list entry; the label steps aside on a leader line ----------
    const anatomy = root.querySelector('[data-anatomy]');
    if (anatomy) {
        let locked = null, shown = null, timer = 0;
        // One mover drives every number and its leader line: the line always runs from its spot on the photo to the number's CURRENT place,
        // so it grows and bends together with the number instead of appearing in full before the number has left.
        const dots = $$('[data-hot]', anatomy).map((d) => {
            const x0 = parseFloat(d.style.getPropertyValue('--x')), y0 = parseFloat(d.style.getPropertyValue('--y'));
            return { n: d.dataset.hot, d, g: anatomy.querySelector(`[data-lead="${d.dataset.hot}"]`), line: anatomy.querySelector(`[data-lead="${d.dataset.hot}"] line`),
                     x0, y0, x1: parseFloat(d.style.getPropertyValue('--sx')), bend: y0 < 45 ? -12 : 12, p: 0, goal: 0, from: 0 };
        });
        const calm = matchMedia('(prefers-reduced-motion: reduce)');
        let spread = false, raf = 0, last = 0;
        const DURATION = 420;
        const place = (t) => {
            const e = t.p * t.p * (3 - 2 * t.p), u = 1 - e;
            const x = u * u * t.x0 + 2 * u * e * ((t.x0 + t.x1) / 2) + e * e * t.x1;
            const y = u * u * t.y0 + 2 * u * e * (t.y0 + t.bend) + e * e * t.y0;
            if (t.p <= 0) { t.d.style.left = ''; t.d.style.top = ''; } else { t.d.style.left = `${x}%`; t.d.style.top = `${y}%`; }
            t.line.setAttribute('x2', (x * 11).toFixed(1)); t.line.setAttribute('y2', (y * 11.4).toFixed(1));
            t.g.style.opacity = t.p > 0 ? (spread ? 0.55 : 0.8) : 0;
        };
        const tick = (now) => {
            const dt = Math.min(48, now - last); last = now;
            let busy = false;
            dots.forEach((t) => {
                if (now < t.from) { busy = true; return; }
                if (t.p !== t.goal) {
                    t.p = t.goal > t.p ? Math.min(t.goal, t.p + dt / DURATION) : Math.max(t.goal, t.p - dt / DURATION);
                    place(t);
                    if (t.p !== t.goal) busy = true;
                }
            });
            raf = busy ? requestAnimationFrame(tick) : 0;
        };
        dots.forEach(place);
        const retarget = (stagger) => {
            const now = performance.now();
            dots.forEach((t, i) => {
                t.goal = spread || t.n === shown ? 1 : 0;
                t.from = stagger ? now + i * 45 : 0;
                if (calm.matches) { t.p = t.goal; place(t); }
            });
            if (!raf && !calm.matches) { last = now; raf = requestAnimationFrame(tick); }
        };
        const show = (n) => {
            shown = n;
            anatomy.classList.toggle('is-lit', !!n);
            $$('[data-hot]', anatomy).forEach((b) => { b.classList.toggle('is-on', b.dataset.hot === n); b.setAttribute('aria-pressed', b.dataset.hot === n ? 'true' : 'false'); });
            $$('[data-item]', anatomy).forEach((li) => { li.classList.toggle('is-on', li.dataset.item === n); li.setAttribute('aria-pressed', li.dataset.item === n ? 'true' : 'false'); });
            $$('[data-lit]', anatomy).forEach((c) => c.classList.toggle('is-on', c.dataset.lit === n));
            retarget(false);
        };
        const pick = (n) => {
            if (locked === n) { locked = null; show(null); return; }      // the same one again: back to the initial state
            locked = n; show(n);
            const li = anatomy.querySelector(`[data-item="${n}"]`);
            if (li) { const r = li.getBoundingClientRect(); if (r.bottom > innerHeight || r.top < 0) li.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
        };
        const peek = (n) => { clearTimeout(timer); show(n); };
        const rest = () => { clearTimeout(timer); clearTimeout(switchTimer); timer = setTimeout(() => show(locked), 160); };
        const mouse = (e) => e.pointerType === 'mouse';
        // hover works on the whole figure, not per shape: the areas overlap and a number runs away from under the pointer, so entering/leaving
        // single elements flickers on the borders. Instead: what is under the pointer is looked up, the current one is kept while the pointer is
        // still on it ("sticky"), and a different one is taken only after the pointer has stayed on it for a moment.
        const stage = anatomy.querySelector('.abm-anatomy__box');
        stage.addEventListener('pointermove', (e) => {
            if (!mouse(e)) return;
            const under = document.elementsFromPoint(e.clientX, e.clientY);
            const found = under.filter((el) => el.dataset && (el.dataset.hit || el.dataset.hot)).map((el) => el.dataset.hit || el.dataset.hot);
            if (!found.length) { clearTimeout(switchTimer); if (shown !== locked) rest(); return; }
            clearTimeout(timer);
            if (found.includes(shown)) { clearTimeout(switchTimer); return; }          // still on the current one
            clearTimeout(switchTimer);
            switchTimer = setTimeout(() => show(found[0]), shown ? 110 : 0);            // the topmost of the rest
        });
        stage.addEventListener('pointerleave', (e) => { if (mouse(e)) rest(); });
        $$('[data-hot], [data-hit]', anatomy).forEach((el) => el.addEventListener('click', () => pick(el.dataset.hot || el.dataset.hit)));
        $$('[data-hot]', anatomy).forEach((b) => {
            b.addEventListener('focus', () => peek(b.dataset.hot));
            b.addEventListener('blur', rest);
        });
        // the eye: every number steps aside at once (and back); the photo is not dimmed
        const eye = anatomy.querySelector('[data-spread]');
        if (eye) {
            eye.addEventListener('click', () => {
                spread = !spread;
                retarget(true);
                eye.setAttribute('aria-pressed', spread ? 'true' : 'false');
                eye.setAttribute('aria-label', spread ? 'Hide labels' : 'Show all labels');
                eye.title = eye.getAttribute('aria-label');
                eye.firstElementChild.outerHTML = spread ? eye.dataset.off : eye.dataset.on;
            });
        }
        const clear = () => { clearTimeout(timer); locked = null; show(null); };
        anatomy.querySelector('.abm-anatomy__box').addEventListener('click', (e) => { if (!e.target.closest('[data-hot], [data-hit]')) clear(); });
        anatomy.addEventListener('keydown', (e) => { if (e.key === 'Escape') clear(); });
        $$('[data-item]', anatomy).forEach((li) => {
            li.tabIndex = 0; li.setAttribute('role', 'button'); li.setAttribute('aria-pressed', 'false');
            li.addEventListener('click', () => pick(li.dataset.item));
            li.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(li.dataset.item); } });
        });
    }

    // ---------- goals: show the chosen goal only ----------
    const goals = root.querySelector('[data-goals]');
    if (goals) {
        const pickBox = goals.querySelector('[data-goalpick]');
        const empty = goals.querySelector('[data-goal-empty]');
        const panels = $$('[data-goal]', goals);
        const radios = $$('input[name="goal"]', goals);
        pickBox.hidden = false;
        const show = (id) => {
            panels.forEach((p) => { p.hidden = p.dataset.goal !== id; });
            empty.hidden = !!id;
            radios.forEach((r) => { r.checked = r.value === id; });
        };
        const fromHash = (location.hash.match(/^#goal-(\w+)$/) || [])[1];
        const start = [fromHash, saved('goal')].find((id) => id && panels.some((p) => p.dataset.goal === id)) || '';
        show(start);
        radios.forEach((r) => r.addEventListener('change', () => {
            show(r.value); save('goal', r.value);
            const p = panels.find((x) => x.dataset.goal === r.value);
            if (p && p.getBoundingClientRect().top > innerHeight * 0.6) p.scrollIntoView({ block: 'start', behavior: 'smooth' });
        }));
    }

    // ---------- tickable lists with a count ----------
    $$('[data-list]').forEach((list) => {
        const id = list.dataset.list;
        const boxes = $$('input[data-id]', list);
        const count = list.querySelector('[data-count]');
        const have = new Set(saved('list.' + id, []));
        boxes.forEach((b) => { b.checked = have.has(b.dataset.id); });
        const update = () => { count.textContent = `${boxes.filter((b) => b.checked).length} of ${boxes.length}`; };
        update();
        boxes.forEach((b) => b.addEventListener('change', () => {
            save('list.' + id, boxes.filter((x) => x.checked).map((x) => x.dataset.id));
            update();
        }));
    });

    // ---------- one shared steeping timer ----------
    const drinks = root.querySelector('[data-drinks]');
    if (drinks) {
        const panel = drinks.querySelector('[data-timer]');
        const nameEl = drinks.querySelector('[data-timer-name]');
        const timeEl = drinks.querySelector('[data-timer-time]');
        const live = drinks.querySelector('[data-timer-live]');
        let end = 0, tick = 0, label = '';
        let muted = false, audio = null;
        const mute = drinks.querySelector('[data-timer-mute]');
        const wake = () => { try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); if (audio.state === 'suspended') audio.resume(); } catch (e) { audio = null; } };
        const chime = () => {
            if (muted || !audio) return;
            const t0 = audio.currentTime;
            [659.25, 783.99, 1046.5].forEach((hz, i) => {
                const o = audio.createOscillator(), g = audio.createGain(), at = t0 + i * 0.32;
                o.type = 'sine'; o.frequency.value = hz;
                g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(0.2, at + 0.04); g.gain.exponentialRampToValueAtTime(0.0001, at + 1.1);
                o.connect(g); g.connect(audio.destination); o.start(at); o.stop(at + 1.2);
            });
        };
        // the signal repeats for about ten seconds, or until «Stop» / a new timer / mute
        let ringing = 0;
        const hush = () => { clearInterval(ringing); ringing = 0; };
        const ring = () => {
            hush();
            chime();
            const until = Date.now() + 10000;
            ringing = setInterval(() => { if (Date.now() + 1100 > until) { hush(); return; } chime(); }, 1300);
        };
        mute.addEventListener('click', () => {
            muted = !muted;
            if (muted) hush();
            mute.setAttribute('aria-pressed', muted ? 'true' : 'false');
            mute.setAttribute('aria-label', muted ? 'Unmute' : 'Mute');
            mute.firstElementChild.outerHTML = muted ? mute.dataset.off : mute.dataset.on;
        });
        const fmt = (ms) => { const s = Math.max(0, Math.ceil(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
        const stop = () => { hush(); clearInterval(tick); tick = 0; panel.hidden = true; panel.classList.remove('is-done'); };
        const step = () => {
            const left = end - Date.now();
            timeEl.textContent = fmt(left);
            if (left <= 0) {
                clearInterval(tick); tick = 0;
                panel.classList.add('is-done');
                timeEl.textContent = 'Done!';
                live.textContent = `${label}: the steeping time is up.`;
                ring();
                try { navigator.vibrate && navigator.vibrate([200, 100, 200, 200, 100, 200]); } catch (e) { /* ignore */ }
            }
        };
        $$('[data-start-timer]', drinks).forEach((b) => {
            b.hidden = false;
            b.addEventListener('click', () => {
                wake(); hush();
                clearInterval(tick);
                label = b.dataset.name;
                end = Date.now() + Number(b.dataset.startTimer) * 60000;
                nameEl.textContent = label;
                panel.classList.remove('is-done');
                panel.hidden = false;
                live.textContent = `Timer started: ${b.dataset.startTimer} minutes.`;
                step();
                tick = setInterval(step, 500);
            });
        });
        drinks.querySelector('[data-timer-stop]').addEventListener('click', stop);
    }

    // ---------- print the cheat-sheet ----------
    $$('[data-print]').forEach((b) => b.addEventListener('click', () => window.print()));
})();
