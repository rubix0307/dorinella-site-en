// The «Gut Massage» player: ONE belly photo, the route of the hand drawn over it and animated in the browser from coordinates
// (course_data/massage.json, served to the admins only) — there is no video file. Eight techniques, a chooser, the written steps
// rendered by the server from the same captions (clicking a step seeks the animation to it), loop-a-step, half speed, keyboard.
// It animates only while on screen and not paused; "reduce motion" shows one still frame and never autoplays.
(() => {
    const hub = document.querySelector('[data-hub]');
    if (!hub) return;
    const NS = 'http://www.w3.org/2000/svg';
    const cl = (v) => Math.max(0, Math.min(1, v));
    const easeCos = (t) => 0.5 - 0.5 * Math.cos(Math.PI * cl(t));
    const easeSoft = (t) => {
        t = cl(t);
        const e = 0.12;
        if (t < e) return (t * t / (2 * e)) / (1 - e);
        if (t > 1 - e) return 1 - ((1 - t) ** 2 / (2 * e)) / (1 - e);
        return (t - e / 2) / (1 - e);
    };
    const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
    const svgEl = (tag, attrs, parent) => {
        const el = document.createElementNS(NS, tag);
        for (const k in attrs) el.setAttribute(k, attrs[k]);
        if (parent) parent.appendChild(el);
        return el;
    };
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const small = window.matchMedia ? window.matchMedia('(max-width: 480px)') : { matches: false };
    // only the viewing preference (half speed) stays in the browser; it is not an answer
    const prefs = {
        get() { try { return JSON.parse(localStorage.getItem('dori.abm.speed') || '{}'); } catch (e) { return {}; } },
        set(v) { try { localStorage.setItem('dori.abm.speed', JSON.stringify(v)); } catch (e) { /* ignore */ } },
    };

    const abm = document.querySelector('[data-abm]');
    const trackUrl = abm && abm.dataset.track;
    const root = hub.querySelector('[data-player]');
    const $ = (name) => root.querySelector(`[data-mp="${name}"]`);
    const chipsBox = hub.querySelector('[data-chips]');
    const chips = Array.from(chipsBox.querySelectorAll('[data-key]'));
    const sections = Array.from(hub.querySelectorAll('[data-tech]'));
    const keys = chips.map((c) => c.dataset.key);

    // ---------- geometry of one technique ----------
    const prepared = new WeakSet();
    function prepare(T) {
        if (prepared.has(T)) return;
        prepared.add(T);
        for (const m of T.moves) {
            const n = m.pts.length / 2;
            m.xs = new Float32Array(n); m.ys = new Float32Array(n); m.cum = new Float32Array(n);
            for (let i = 0; i < n; i++) {
                m.xs[i] = m.pts[2 * i]; m.ys[i] = m.pts[2 * i + 1];
                if (i) m.cum[i] = m.cum[i - 1] + Math.hypot(m.xs[i] - m.xs[i - 1], m.ys[i] - m.ys[i - 1]);
            }
            m.L = m.cum[n - 1]; m.E = m.ease === 'soft' ? easeSoft : easeCos;
        }
    }
    const idxAt = (m, s) => {
        let lo = 0, hi = m.cum.length - 1;
        while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (m.cum[mid] <= s) lo = mid; else hi = mid; }
        return lo;
    };
    const pointAt = (m, s) => {
        s = Math.max(0, Math.min(m.L, s));
        const i = idxAt(m, s), j = Math.min(i + 1, m.cum.length - 1);
        const seg = m.cum[j] - m.cum[i] || 1, f = (s - m.cum[i]) / seg;
        return [m.xs[i] + (m.xs[j] - m.xs[i]) * f, m.ys[i] + (m.ys[j] - m.ys[i]) * f];
    };
    const slicePts = (m, s0, s1) => {
        const a = pointAt(m, s0), out = [a[0].toFixed(1) + ',' + a[1].toFixed(1)];
        for (let i = idxAt(m, s0) + 1; i < m.cum.length && m.cum[i] < s1; i++) out.push(m.xs[i].toFixed(1) + ',' + m.ys[i].toFixed(1));
        const b = pointAt(m, s1); out.push(b[0].toFixed(1) + ',' + b[1].toFixed(1));
        return out.join(' ');
    };

    function start(DATA) {
        const techs = DATA.techniques;
        const world = $('world'), glowG = $('glow'), warmEl = $('warm'), tipsG = $('tips'), gridG = $('grid');
        const scrub = $('scrub'), segBox = $('seg'), play = $('play'), loopBtn = $('loop'), speedBtn = $('speed'), live = $('live');
        const glowPool = [], tipPool = [];
        const glowLine = (i) => {
            while (glowPool.length <= i) glowPool.push(svgEl('polyline', { fill: 'none', stroke: 'rgb(176,148,130)', 'stroke-linejoin': 'round' }, glowG));
            return glowPool[i];
        };
        const tipEl = (i) => {
            while (tipPool.length <= i) {
                const g = svgEl('g', {}, tipsG);
                tipPool.push({
                    g,
                    halo: svgEl('circle', { fill: '#fff', filter: 'url(#mp-halo)' }, g),
                    ring: svgEl('circle', { fill: '#fff', stroke: '#fff', 'stroke-width': '3.3' }, g),
                    dot: svgEl('circle', { r: '6.6', fill: '#0f7560' }, g),
                });
            }
            return tipPool[i];
        };
        const lenses = [0, 1].map((i) => ({ g: $('lens' + i), c: $('lc' + i), use: $('lens' + i).firstElementChild }));

        let cur = null, curKey = '', t = 0, paused = reduce, speed = 1, loop = null, visible = false, endHold = 0, capIdx = -2;
        let last = performance.now(), lastDraw = 0, announced = false;
        let stepItems = [];

        // ---------- how long each technique is really watched: on screen, playing, tab in front ----------
        let report = { key: '', seconds: 0, completed: false, plays: 0 }, sessionSecs = 0, sinceFlush = 0;
        const flush = () => {
            if (!trackUrl || !report.key || (report.seconds < 0.5 && !report.completed && !report.plays)) { report.seconds = 0; report.completed = false; report.plays = 0; return; }
            const body = JSON.stringify({ key: report.key, seconds: Math.round(report.seconds * 10) / 10, completed: report.completed, plays: report.plays });
            report.seconds = 0; report.completed = false; report.plays = 0; sinceFlush = 0;
            try {
                if (!(navigator.sendBeacon && navigator.sendBeacon(trackUrl, new Blob([body], { type: 'application/json' })))) {
                    fetch(trackUrl, { method: 'POST', credentials: 'same-origin', keepalive: true, headers: { 'Content-Type': 'application/json' }, body });
                }
            } catch (e) { /* statistics must never get in the way */ }
        };
        const watched = (dt) => {
            if (report.key !== curKey) { flush(); report.key = curKey; sessionSecs = 0; }
            report.seconds += dt; sessionSecs += dt; sinceFlush += dt;
            if (!report.plays && sessionSecs >= 5 && !seenKeys.has(curKey)) { seenKeys.add(curKey); report.plays = 1; }
            if (sinceFlush >= 30) flush();
        };
        const seenKeys = new Set();
        document.addEventListener('visibilitychange', () => { if (document.hidden) flush(); });
        addEventListener('pagehide', flush);

        const saved = prefs.get();
        if (saved.speed === 0.5) { speed = 0.5; speedBtn.setAttribute('aria-pressed', 'true'); }

        function buildGrid() {
            gridG.replaceChildren();
            const G = cur.grid;
            if (!G) return;
            G.lines = svgEl('g', { stroke: '#fff', 'stroke-width': '3.3', 'stroke-dasharray': '10 8', fill: 'none' }, gridG);
            svgEl('line', { x1: G.c[0] - G.half, y1: G.c[1], x2: G.c[0] + G.half, y2: G.c[1] }, G.lines);
            svgEl('line', { x1: G.c[0], y1: G.c[1] - G.half, x2: G.c[0], y2: G.c[1] + G.half }, G.lines);
            G.rects = G.slots.map((s) => svgEl('rect', {
                x: s[2][0] - G.s, y: s[2][1] - G.s, width: 2 * G.s, height: 2 * G.s, rx: 18,
                fill: '#fff', stroke: '#fff', 'stroke-width': '3.3', opacity: 0, 'fill-opacity': 0.18,
            }, gridG));
        }

        // caption index -> index in the written step list (only captions with a label are steps)
        const labelled = () => cur.captions.map((c, i) => (c[1] ? i : -1)).filter((i) => i >= 0);

        // the progress bar is cut into one segment per step, as long as the step lasts; switching to a technique with other steps
        // morphs the segments (they grow, shrink, appear and fold away) instead of jumping
        const segs = [];
        function buildSegments(animate) {
            const order = labelled();
            const starts = order.map((i) => cur.captions[i][0]);
            const bounds = starts.length ? starts.map((s, i) => [i ? s : 0, i + 1 < starts.length ? starts[i + 1] : cur.total]) : [[0, cur.total]];
            while (segs.length < bounds.length) {
                const el = document.createElement('span');
                el.className = 'abm-seg__part';
                el.innerHTML = '<i></i>';
                el.style.flexGrow = animate ? '0' : '1';
                segBox.appendChild(el);
                segs.push({ el, fill: el.firstChild, s: 0, e: 1 });
                if (animate) el.getBoundingClientRect();               // start from zero width, then grow
            }
            bounds.forEach(([s, e], i) => {
                const sg = segs[i];
                sg.s = s; sg.e = e;
                sg.el.classList.remove('is-gone');
                sg.el.style.flexGrow = String(Math.max(0.001, e - s));
            });
            segs.splice(bounds.length).forEach((sg) => {                   // the steps this technique does not have fold away
                sg.el.classList.add('is-gone');
                sg.el.style.flexGrow = '0';
                setTimeout(() => sg.el.remove(), 700);
            });
        }
        function drawSegments() {
            segs.forEach((sg) => {
                const f = cl((t - sg.s) / Math.max(0.001, sg.e - sg.s));
                sg.fill.style.transform = `scaleX(${f.toFixed(4)})`;
                sg.el.classList.toggle('is-now', t >= sg.s && (t < sg.e || sg.e >= cur.total));
                sg.el.classList.toggle('is-loop', !!loop && loop.from >= sg.s - 0.01 && loop.from < sg.e - 0.01);
            });
        }

        function draw() {
            const T = cur;
            let b = 0;
            if (T.breath) for (const [i0, i1, e0, e1] of T.breath) {
                if (t >= i0 && t < i1) b = 0.055 * easeCos((t - i0) / (i1 - i0));
                else if (t >= i1 && t < e0) b = 0.055;
                else if (t >= e0 && t < e1) b = 0.055 * (1 - easeCos((t - e0) / (e1 - e0)));
            }
            world.setAttribute('transform', b > 0.0005 && !reduce ? `translate(460 645) scale(${(1 + b * 0.35).toFixed(4)}) translate(-460 -645)` : '');

            let wk = 0;
            for (const [t0, t1] of T.warm) if (t >= t0 && t <= t1 + 0.8)
                wk = cl((t - t0) / 0.8) * cl((t1 + 0.8 - t) / 0.8) * (0.85 + 0.15 * Math.sin((t - t0) * 3));
            warmEl.setAttribute('opacity', wk.toFixed(3));

            let gi = 0;
            const tips = [];
            const N = small.matches ? 6 : 12;
            for (const m of T.moves) {
                if (t < m.t0) continue;
                const p = m.E((t - m.t0) / m.dur), sTip = p * m.L, done = t - (m.t0 + m.dur);
                const fade = done < 0 ? 1 : cl(1 - done / 0.9);
                if (m.glow && fade > 0 && sTip > 0.5) {
                    const Tr = m.trail, s0 = Math.max(0, sTip - Tr), ch = (sTip - s0) / N;
                    for (let k = 0; k < N; k++) {
                        const a = s0 + k * ch, e = Math.min(a + ch + 1.5, sTip), behind = sTip - (a + ch / 2);
                        const inten = (Math.exp(-behind / (Tr * 0.45)) * 0.85 + 0.15) * fade * (done < 0 ? 1 : 0.5 + 0.5 * fade);
                        const op = Math.min(1, inten * 1.25);
                        if (op < 0.03 || e - a < 0.3) continue;
                        const ln = glowLine(gi++);
                        ln.setAttribute('points', slicePts(m, a, e));
                        ln.setAttribute('stroke-width', k === N - 1 ? 95 : 88);
                        ln.setAttribute('stroke-linecap', k === 0 || k === N - 1 ? 'round' : 'butt');
                        ln.setAttribute('stroke-opacity', op.toFixed(3));
                        ln.removeAttribute('visibility');
                    }
                }
                if (done < 0.7) {
                    let ta = m.tip ? cl((t - m.t0) / 0.3) : 0;
                    if (done > 0) ta *= cl(1 - done / 0.6);
                    const pulse = m.pulse ? 0.5 + 0.5 * Math.sin((t - m.t0) * 2 * Math.PI * 1.2) : 0;
                    const press = m.press * (done < 0 ? 1 : cl(1 - done / 0.4)) * (1 + m.pulse * (pulse - 0.5) * 2);
                    const [x, y] = pointAt(m, sTip);
                    tips.push({ x, y, a: ta, press, pulse });
                }
            }
            for (let i = gi; i < glowPool.length; i++) glowPool[i].setAttribute('visibility', 'hidden');
            for (const [t0, t1, pt] of T.touches) if (t >= t0 - 0.3 && t <= t1 + 0.6)
                tips.push({ x: pt[0], y: pt[1], a: cl((t - t0 + 0.3) / 0.4) * cl((t1 + 0.6 - t) / 0.6), press: 0, pulse: 0 });

            let li = 0;
            if (!small.matches) {
                for (const tp of tips) {
                    const amt = tp.press * tp.a;
                    if (amt < 0.004 || li > 1) continue;
                    const L = lenses[li++], s = 1 / (1 - amt);
                    L.c.setAttribute('cx', tp.x.toFixed(1)); L.c.setAttribute('cy', tp.y.toFixed(1));
                    L.use.setAttribute('transform', `translate(${tp.x.toFixed(1)} ${tp.y.toFixed(1)}) scale(${s.toFixed(4)}) translate(${(-tp.x).toFixed(1)} ${(-tp.y).toFixed(1)})`);
                    L.g.removeAttribute('visibility');
                }
            }
            for (; li < 2; li++) lenses[li].g.setAttribute('visibility', 'hidden');

            let ti = 0;
            for (const tp of tips) {
                if (tp.a < 0.01) continue;
                const el = tipEl(ti++), R = 23 * (1 + 0.07 * Math.sin(t * 6)) * (1 - 0.18 * tp.pulse);
                for (const c of [el.halo, el.ring, el.dot]) { c.setAttribute('cx', tp.x.toFixed(1)); c.setAttribute('cy', tp.y.toFixed(1)); }
                el.halo.setAttribute('r', (R + 8).toFixed(1)); el.halo.setAttribute('fill-opacity', (0.43 * tp.a).toFixed(3));
                el.ring.setAttribute('r', R.toFixed(1));
                el.ring.setAttribute('fill-opacity', (0.59 * tp.a).toFixed(3)); el.ring.setAttribute('stroke-opacity', (0.92 * tp.a).toFixed(3));
                el.dot.setAttribute('fill-opacity', (0.9 * tp.a).toFixed(3));
                el.g.removeAttribute('visibility');
            }
            for (let i = ti; i < tipPool.length; i++) tipPool[i].g.setAttribute('visibility', 'hidden');

            if (T.grid && T.grid.lines) {
                const G = T.grid;
                G.lines.setAttribute('stroke-opacity', (0.78 * cl((t - G.from) / 0.6)).toFixed(3));
                G.slots.forEach((s, i) => {
                    const k = t >= s[0] && t <= s[1] ? cl((t - s[0]) / 0.3) * cl((s[1] - t) / 0.3) : 0;
                    G.rects[i].setAttribute('opacity', (0.9 * k).toFixed(3));
                });
            }

            let ci = -1;
            T.captions.forEach((c, i) => { if (t >= c[0]) ci = i; });
            if (ci !== capIdx) {
                capIdx = ci;
                const c = T.captions[ci];
                $('step').textContent = c ? c[1] : ''; $('step').hidden = !c || !c[1];
                $('desc').textContent = c ? c[2] : '';
                markStep(ci);
            }
            $('cap').style.opacity = ci >= 0 ? cl((t - T.captions[ci][0]) / 0.4) : 0;

            const intro = $('intro');
            if (T.intro && t < 2) { intro.hidden = false; intro.textContent = T.intro; intro.style.opacity = cl((2 - t) / 0.4); } else intro.hidden = true;

            const pill = $('pill');
            let pa = 0, ptxt = '';
            if (T.pills) for (const [p0, p1, txt] of T.pills) if (t >= p0 && t < p1) { pa = cl((t - p0) / 0.4) * cl((p1 - t) / 0.35); ptxt = txt; }
            pill.hidden = pa < 0.01;
            if (!pill.hidden) { pill.textContent = ptxt; pill.style.opacity = pa; }

            scrub.value = t;
            drawSegments();
            scrub.setAttribute('aria-valuetext', `${fmt(t)} of ${fmt(T.total)}`);
            $('time').textContent = `${fmt(t)} / ${fmt(T.total)}`;
        }

        // highlight the current step in the written list and announce it once per change
        function markStep(ci) {
            const order = labelled();
            const at = order.indexOf(ci);
            stepItems.forEach((li, i) => {
                const on = i === at;
                li.classList.toggle('is-current', on);
                if (on) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
            });
            if (at >= 0 && announced) live.textContent = `Step ${at + 1} of ${order.length}`;
        }

        // ---------- loop one step ----------
        function stepWindow() {
            const order = labelled();
            if (!order.length) return null;
            let at = order.indexOf(capIdx);
            if (at < 0) at = capIdx < 0 ? 0 : order.findIndex((i) => i > capIdx);
            if (at < 0) at = order.length - 1;
            const startAt = cur.captions[order[at]][0];
            const next = cur.captions[order[at] + 1];
            return { from: startAt, to: next ? next[0] : cur.total };
        }
        function setLoop(on) {
            loop = on ? stepWindow() : null;
            loopBtn.setAttribute('aria-pressed', loop ? 'true' : 'false');
        }
        loopBtn.addEventListener('click', () => setLoop(!loop));
        speedBtn.addEventListener('click', () => {
            speed = speed === 1 ? 0.5 : 1;
            speedBtn.setAttribute('aria-pressed', speed === 0.5 ? 'true' : 'false');
            prefs.set({ speed });
        });

        function setPaused(v) {
            paused = v;
            play.setAttribute('aria-label', v ? play.dataset.labelPlay : play.dataset.labelPause);
            $('playIcon').innerHTML = v ? '<path d="M8 5v14l11-7z"/>' : '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>';
        }

        // ---------- choosing a technique ----------
        function select(key, opts = {}) {
            if (!techs[key]) key = keys[0];
            flush();
            const T = techs[key];
            prepare(T);
            cur = T; curKey = key;
            t = reduce ? T.total * 0.45 : 0; endHold = 0; capIdx = -2; announced = false;
            setLoop(false);
            const number = keys.indexOf(key) + 1;
            $('tag').textContent = `TECHNIQUE #${number} · ${T.short.toUpperCase()}`;
            $('title').textContent = T.title;
            root.querySelector('[data-mp="svg"]').setAttribute('aria-label', `Animation: ${T.title}`);
            scrub.max = T.total;
            buildSegments(segs.length > 0);
            glowPool.forEach((l) => l.setAttribute('visibility', 'hidden'));
            buildGrid();
            chips.forEach((c) => { const on = c.dataset.key === key; c.setAttribute('aria-checked', on ? 'true' : 'false'); c.tabIndex = on ? 0 : -1; });
            sections.forEach((s) => s.classList.toggle('is-current', s.dataset.tech === key));
            const section = sections.find((s) => s.dataset.tech === key);
            hub.querySelectorAll('.abm-steps li.is-current').forEach((li) => { li.classList.remove('is-current'); li.removeAttribute('aria-current'); });
            stepItems = Array.from(section.querySelectorAll('.abm-steps li'));
            if (!opts.fromHash) { try { history.replaceState(null, '', '#' + key); } catch (e) { /* ignore */ } }
            draw();
        }

        // written steps: each becomes a button that seeks to it
        sections.forEach((section) => {
            section.querySelectorAll('.abm-steps li').forEach((li) => {
                const btn = document.createElement('button');
                btn.type = 'button';
                while (li.firstChild) btn.appendChild(li.firstChild);
                li.appendChild(btn);
                btn.addEventListener('click', () => {
                    if (section.dataset.tech !== curKey) select(section.dataset.tech, { fromHash: false });
                    const at = Number(li.dataset.t);
                    t = reduce ? at + 1.5 : at + 0.05; endHold = 0;
                    setLoop(false);
                    if (!reduce) setPaused(false);
                    announced = true; draw(); announced = false;
                    // on a phone the player is above the list: bring it back into view so the jump is seen
                    const r = root.getBoundingClientRect();
                    if (r.bottom < 80 || r.top > innerHeight - 80) root.scrollIntoView({ block: 'start', behavior: reduce ? 'auto' : 'smooth' });
                });
            });
        });

        chips.forEach((c) => c.addEventListener('click', () => { if (c.dataset.key !== curKey) select(c.dataset.key); }));
        chipsBox.hidden = false;
        chipsBox.addEventListener('keydown', (e) => {
            const i = chips.indexOf(document.activeElement);
            if (i < 0) return;
            let j = -1;
            if (e.key === 'ArrowRight' || e.key === 'ArrowDown') j = (i + 1) % chips.length;
            else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') j = (i - 1 + chips.length) % chips.length;
            else if (e.key === 'Home') j = 0;
            else if (e.key === 'End') j = chips.length - 1;
            if (j >= 0) { e.preventDefault(); chips[j].focus(); select(chips[j].dataset.key); }
        });

        // ---------- transport ----------
        play.addEventListener('click', () => { if (paused && t >= cur.total) { t = 0; endHold = 0; } setPaused(!paused); draw(); });
        $('restart').addEventListener('click', () => { t = 0; endHold = 0; setPaused(false); draw(); });
        scrub.addEventListener('input', (e) => {
            t = parseFloat(e.target.value) || 0; endHold = 0;
            if (loop && (t < loop.from || t > loop.to)) setLoop(false);
            draw();
        });

        // keys work only while focus is inside the player or the chooser (never on the whole document)
        const seekBy = (d) => { t = Math.max(0, Math.min(cur.total, t + d)); endHold = 0; if (loop && (t < loop.from || t > loop.to)) setLoop(false); draw(); };
        scrub.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { e.preventDefault(); seekBy(-2); }
            else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { e.preventDefault(); seekBy(2); }
        });
        const stepBy = (dir) => {
            const order = labelled();
            const starts = order.map((i) => cur.captions[i][0]);
            let target;
            if (dir > 0) target = starts.find((s) => s > t + 0.2);
            else { const before = starts.filter((s) => s < t - 0.8); target = before.length ? before[before.length - 1] : 0; }
            if (target === undefined) return;
            t = target + 0.05; endHold = 0; setLoop(false); announced = true; draw(); announced = false;
        };
        hub.addEventListener('keydown', (e) => {
            if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
            const el = e.target;
            if (!el.closest('[data-player], [data-chips]')) return;
            if (el.matches('input[type="text"], textarea, select')) return;
            const onRange = el.matches('input[type="range"]');
            const onChip = !!el.closest('[data-chips]');
            switch (e.key) {
                case ' ': if (el.matches('button')) return; e.preventDefault(); setPaused(!paused); draw(); break;
                case 'k': case 'K': setPaused(!paused); draw(); break;
                case 'ArrowLeft': if (onRange || onChip) return; e.preventDefault(); seekBy(-2); break;
                case 'ArrowRight': if (onRange || onChip) return; e.preventDefault(); seekBy(2); break;
                case '[': stepBy(-1); break;
                case ']': stepBy(1); break;
                case 'l': case 'L': setLoop(!loop); break;
                case '.': speedBtn.click(); break;
                default:
                    if (/^[1-8]$/.test(e.key) && keys[Number(e.key) - 1]) select(keys[Number(e.key) - 1]);
            }
        });

        // ---------- the loop of frames ----------
        const tick = (now) => {
            const dt = Math.min(0.1, (now - last) / 1000);
            last = now;
            if (visible && !paused && !document.hidden) {
                watched(dt);
                t += dt * speed;
                if (loop) {
                    if (t >= loop.to) { t = loop.from; }
                } else if (t >= cur.total) {
                    if (endHold === 0 && report.key === curKey) { report.completed = true; flush(); }
                    t = cur.total; endHold += dt;
                    if (endHold > 1.2) { t = 0; endHold = 0; }
                }
                if (!small.matches || now - lastDraw >= 33) { lastDraw = now; draw(); }
            }
            requestAnimationFrame(tick);
        };
        if ('IntersectionObserver' in window) {
            new IntersectionObserver((entries) => { visible = entries.some((e) => e.isIntersecting); }, { threshold: 0.25 }).observe(root);
        } else {
            visible = true;
        }

        // ---------- the address: #m1…#m8 ----------
        const fromHash = () => { const m = location.hash.match(/^#(m[1-8])$/); return m && techs[m[1]] ? m[1] : ''; };
        const arrive = (key) => {
            select(key, { fromHash: true });
            root.scrollIntoView({ block: 'start', behavior: reduce ? 'auto' : 'smooth' });
        };
        window.addEventListener('hashchange', () => { const k = fromHash(); if (k && k !== curKey) arrive(k); });

        setPaused(reduce);
        const first = fromHash();
        select(first || keys[0], { fromHash: true });
        if (first) setTimeout(() => root.scrollIntoView({ block: 'start', behavior: 'auto' }), 60);
        requestAnimationFrame((n) => { last = n; tick(n); });
    }

    fetch(hub.dataset.src, { credentials: 'same-origin' })
        .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(start)
        .catch(() => { hub.classList.add('abm-hub--off'); });
})();
