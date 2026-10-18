// Massage technique player: a photo of the belly with the hand's route drawn over it, animated in the browser from the
// route's coordinates (static/data/*.json) — there is no video file. Markup: templates/includes/materials/_player.html.
// The animation runs only while the player is on screen and not paused; "reduce motion" shows one still frame.
(() => {
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

    function prepare(T) {
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

    function start(root, T) {
        prepare(T);
        const $ = (name) => root.querySelector(`[data-mp="${name}"]`);
        const world = $('world'), glowG = $('glow'), warmEl = $('warm'), tipsG = $('tips');
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
                    halo: svgEl('circle', { fill: '#fff', filter: `url(#${root.dataset.mpId}-halo)` }, g),
                    ring: svgEl('circle', { fill: '#fff', stroke: '#fff', 'stroke-width': '3.3' }, g),
                    dot: svgEl('circle', { r: '6.6', fill: '#0f7560' }, g),
                });
            }
            return tipPool[i];
        };
        const lenses = [0, 1].map((i) => ({ g: $('lens' + i), c: $('lc' + i), use: $('lens' + i).firstElementChild }));

        let t = reduce ? T.total * 0.45 : 0, paused = reduce, visible = false, endHold = 0, capIdx = -2, last = performance.now();

        $('tag').textContent = T.tag;
        $('title').textContent = T.title;
        const scrub = $('scrub');
        scrub.max = T.total;

        function draw() {
            let b = 0;
            if (T.breath) for (const [i0, i1, e0, e1] of T.breath) {
                if (t >= i0 && t < i1) b = 0.055 * easeCos((t - i0) / (i1 - i0));
                else if (t >= i1 && t < e0) b = 0.055;
                else if (t >= e0 && t < e1) b = 0.055 * (1 - easeCos((t - e0) / (e1 - e0)));
            }
            world.setAttribute('transform', b > 0.0005 ? `translate(460 645) scale(${(1 + b * 0.35).toFixed(4)}) translate(-460 -645)` : '');

            let wk = 0;
            for (const [t0, t1] of T.warm) if (t >= t0 && t <= t1 + 0.8)
                wk = cl((t - t0) / 0.8) * cl((t1 + 0.8 - t) / 0.8) * (0.85 + 0.15 * Math.sin((t - t0) * 3));
            warmEl.setAttribute('opacity', wk.toFixed(3));

            let gi = 0;
            const tips = [];
            for (const m of T.moves) {
                if (t < m.t0) continue;
                const p = m.E((t - m.t0) / m.dur), sTip = p * m.L, done = t - (m.t0 + m.dur);
                const fade = done < 0 ? 1 : cl(1 - done / 0.9);
                if (m.glow && fade > 0 && sTip > 0.5) {
                    const Tr = m.trail, s0 = Math.max(0, sTip - Tr), N = 12, ch = (sTip - s0) / N;
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
            for (const tp of tips) {
                const amt = tp.press * tp.a;
                if (amt < 0.004 || li > 1) continue;
                const L = lenses[li++], s = 1 / (1 - amt);
                L.c.setAttribute('cx', tp.x.toFixed(1)); L.c.setAttribute('cy', tp.y.toFixed(1));
                L.use.setAttribute('transform', `translate(${tp.x.toFixed(1)} ${tp.y.toFixed(1)}) scale(${s.toFixed(4)}) translate(${(-tp.x).toFixed(1)} ${(-tp.y).toFixed(1)})`);
                L.g.removeAttribute('visibility');
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

            let ci = -1;
            T.captions.forEach((c, i) => { if (t >= c[0]) ci = i; });
            if (ci !== capIdx) {
                capIdx = ci;
                const c = T.captions[ci];
                $('step').textContent = c ? c[1] : ''; $('step').hidden = !c || !c[1];
                $('desc').textContent = c ? c[2] : '';
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
            $('time').textContent = `${fmt(t)} / ${fmt(T.total)}`;
        }

        const play = $('play');
        function setPaused(v) {
            paused = v;
            play.setAttribute('aria-label', v ? play.dataset.labelPlay : play.dataset.labelPause);
            $('playIcon').innerHTML = v ? '<path d="M8 5v14l11-7z"/>' : '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>';
        }
        setPaused(paused);
        play.addEventListener('click', () => { if (paused && t >= T.total) { t = 0; endHold = 0; } setPaused(!paused); draw(); });
        $('restart').addEventListener('click', () => { t = 0; endHold = 0; setPaused(false); draw(); });
        scrub.addEventListener('input', (e) => { t = parseFloat(e.target.value) || 0; endHold = 0; draw(); });

        const tick = (now) => {
            const dt = Math.min(0.1, (now - last) / 1000);
            last = now;
            if (visible && !paused) {
                t += dt;
                if (t >= T.total) { t = T.total; endHold += dt; if (endHold > 1.2) { t = 0; endHold = 0; } }
                draw();
            }
            requestAnimationFrame(tick);
        };
        draw();
        if ('IntersectionObserver' in window) {
            new IntersectionObserver((entries) => { visible = entries.some((e) => e.isIntersecting); }, { threshold: 0.25 }).observe(root);
        } else {
            visible = true;
        }
        requestAnimationFrame((n) => { last = n; tick(n); });
    }

    document.querySelectorAll('.mplayer[data-src]').forEach((root) => {
        fetch(root.dataset.src).then((r) => r.json()).then((T) => start(root, T)).catch(() => root.classList.add('mplayer--off'));
    });
})();
