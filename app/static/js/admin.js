(function () {
    'use strict';

    /* the "skip to content" link of base.html: the page's <main> is its target */
    const main = document.querySelector('main.admin-main');
    if (main) { main.id = 'admin-content'; main.tabIndex = -1; }

    /* ---------- time zone: the server counts days and hours in the browser's zone ---------- */
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    // the zone the server was asked for (it may render a renamed or fallback zone — that's fine, no loop)
    const rendered = document.querySelector('[data-tz]')?.dataset.tzAsked;
    if (zone) {
        document.cookie = `admin_tz=${zone}; path=/admin; max-age=31536000; samesite=lax`;   // IANA names are cookie-safe
        document.querySelectorAll('[data-browser-zone]').forEach((option) => { option.textContent = t('Browser time zone (%(zone)s)', {zone}); });
        let tried = false;
        try { tried = sessionStorage.getItem('admin-tz') === zone; } catch { /* storage blocked */ }
        if (rendered && rendered !== zone && !tried) {        // rendered in another zone: once more, in ours
            try { sessionStorage.setItem('admin-tz', zone); } catch { /* storage blocked */ }
            location.reload();
            return;
        }
    }

    const SVG = 'http://www.w3.org/2000/svg';
    // the lines of each chart a tile can pick (colours validated for CVD and contrast, see admin.css)
    const CHART_SERIES = {
        views: [{key: 'v', name: t('Views'), colorVar: '--chart-views', area: true}],
        visitors: [{key: 'v', name: t('Visitors'), colorVar: '--chart-visitors', area: true}],
        clicks: [
            {key: 'c', name: t('Clicks'), colorVar: '--chart-clicks', area: true},
            {key: 'u', name: t('Unique'), colorVar: '--chart-visitors'},
        ],
    };
    const weekday = (d) => d.toLocaleDateString(adminLang, {weekday: 'short'});

    const el = (name, attrs = {}, parent = null) => {
        const node = document.createElementNS(SVG, name);
        Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
        parent?.appendChild(node);
        return node;
    };
    // every point is the start of its bucket ('2026-10-02T14:15'): 15 minutes on a one-day chart, 4 hours longer
    const pad = (n) => String(n).padStart(2, '0');
    const parseAt = (iso) => new Date(`${iso}:00`);
    const dayMonth = (d) => `${pad(d.getDate())}.${pad(d.getMonth() + 1)}`;
    const hhmm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const dayIndex = (a, b) => Math.round((Date.UTC(b.getFullYear(), b.getMonth(), b.getDate())
        - Date.UTC(a.getFullYear(), a.getMonth(), a.getDate())) / 864e5);
    const MAX_DAYS_BACK = 366;          // as far as the one-day chart scrolls into the past (admin.MAX_CHART_DAYS_BACK)
    const DAYS_AHEAD_OF_SCROLL = 14;    // days of room laid out at once; loading fills them without resizing anything

    // round axis maximum: 1, 2, 2.5, 5, 10 × 10^n, so ticks land on clean numbers
    function niceScale(max, ticks = 4) {
        if (max <= 0) return {top: ticks, step: 1};
        const raw = max / ticks;
        const mag = 10 ** Math.floor(Math.log10(raw));
        const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw && (s >= 1 || mag < 1));
        const intStep = Math.max(1, Math.ceil(step));
        return {top: intStep * Math.ceil(max / intStep), step: intStep};
    }

    /* ---------- the chart: lines on one axis, crosshair tooltip; the one-day chart scrolls into past days ---------- */
    function drawChart(box) {
        const charts = box.chartState ??= JSON.parse(box.querySelector('[data-chart-data]').textContent || '{}');
        const metric = CHART_SERIES[box.dataset.metric] ? box.dataset.metric : 'views';
        const SERIES = CHART_SERIES[metric];
        const data = charts[metric] || [];
        // the scroller survives redraws (a finger on it keeps scrolling); only its picture is replaced
        let scroller = box.querySelector('.chart-scroll');
        const fromRight = scroller ? scroller.scrollWidth - scroller.scrollLeft : null;
        box.querySelector('.chart-y')?.remove();
        box.querySelector('.chart-tooltip')?.remove();
        if (!data.length) { scroller?.remove(); return; }

        const css = getComputedStyle(box);                 // SVG attributes don't resolve var()
        const color = (s) => css.getPropertyValue(s.colorVar).trim();
        const surface = css.getPropertyValue('--surface').trim();
        const width = box.clientWidth;
        if (width < 120) return;   // not laid out yet (hidden / mid-swap); the ResizeObserver redraws
        const height = width < 560 ? 240 : 300;
        const step = charts.step || 1440;                   // minutes per point
        const oneDay = step <= 15;                           // the day-by-day chart that scrolls into the past
        const perDay = 1440 / step;
        const m = {top: oneDay ? 24 : 14, right: width < 560 ? 16 : (oneDay ? 24 : 96), bottom: 28, left: 40};
        const viewW = width - m.left - m.right;              // one screen of plot
        const h = height - m.top - m.bottom;

        // where each point sits on a continuous grid that starts at the first day's midnight; the one-day
        // chart's grid reaches `span` days back from the start, loaded or not, so loading never resizes it
        const first = parseAt(data[0].d);
        const lastAt = parseAt(data[data.length - 1].d);
        const lastDay = new Date(lastAt.getFullYear(), lastAt.getMonth(), lastAt.getDate());
        // never further back than the first day anything was recorded: there the history begins
        const historyDays = charts.history_start ? dayIndex(parseAt(`${charts.history_start}T00:00`), lastDay) + 1 : MAX_DAYS_BACK;
        if (oneDay) {
            charts.span = Math.min(historyDays, Math.max(charts.span || DAYS_AHEAD_OF_SCROLL, dayIndex(first, lastDay) + 1));
        }
        const atHistoryStart = oneDay && charts.span >= historyDays;
        const firstDay = oneDay
            ? new Date(lastDay.getFullYear(), lastDay.getMonth(), lastDay.getDate() - charts.span + 1)
            : new Date(first.getFullYear(), first.getMonth(), first.getDate());
        const slotOf = (iso) => {
            const d = parseAt(iso);
            return dayIndex(firstDay, d) * perDay + Math.round((d.getHours() * 60 + d.getMinutes()) / step);
        };
        const slots = data.map((p) => slotOf(p.d));
        const days = oneDay ? charts.span : 0;
        const loadedFrom = oneDay ? dayIndex(firstDay, first) : 0;    // the first day with data on the grid
        const total = oneDay ? days * perDay : Math.max(charts.slots || 0, slots[slots.length - 1] + 1);
        const unit = oneDay ? viewW / (perDay - 1) : viewW / Math.max(1, total - 1);   // px per slot
        const plotW = (total - 1) * unit;
        const svgW = m.left + plotW + m.right;
        const x = (slot) => m.left + slot * unit;

        // the value scale fits what's on screen (a scrolled one-day chart: the visible days only)
        const visible = (() => {
            if (!oneDay || !scroller) return data;
            const from = (scroller.scrollLeft - m.left) / unit - 1;
            const to = from + viewW / unit + 2;
            const seen = data.filter((p, i) => slots[i] >= from && slots[i] <= to);
            return seen.length ? seen : data;
        })();
        const {top, step: yStep} = niceScale(Math.max(...visible.flatMap((d) => SERIES.map((s) => d[s.key] ?? 0))));
        const y = (v) => m.top + h - (v / top) * h;

        if (!scroller) {
            scroller = document.createElement('div');
            scroller.className = 'chart-scroll';
        }
        const svg = el('svg', {viewBox: `0 0 ${svgW} ${height}`, width: svgW, height, role: 'img', tabindex: '0',
            'aria-label': t('Chart: %(series)s, %(step)s. The arrow keys show the values.', {series: SERIES.map((s) => s.name.toLowerCase()).join(', '),
                step: oneDay ? t('every 15 minutes, scrolls back through earlier days') : t('every 4 hours')})});
        scroller.replaceChildren(svg);

        for (let v = 0; v <= top; v += yStep) el('line', {class: 'grid', x1: m.left, x2: m.left + plotW, y1: y(v), y2: y(v)}, svg);

        if (oneDay) {
            // a day per screen: hour ticks, a line and the date where each day begins
            const everyH = Math.max(1, Math.ceil(24 / Math.max(2, Math.floor(viewW / 70))));
            for (let d = 0; d < days; d += 1) {
                const day = new Date(firstDay.getFullYear(), firstDay.getMonth(), firstDay.getDate() + d);
                const x0 = x(d * perDay);
                if (d) el('line', {class: 'day-line', x1: x0 - unit / 2, x2: x0 - unit / 2, y1: 4, y2: m.top + h}, svg);
                const label = el('text', {class: 'day-label', x: x0 + 2, y: 14}, svg);
                label.textContent = `${weekday(day)}, ${dayMonth(day)}`;
                if (d === 0 && atHistoryStart) {   // nothing was recorded before this day
                    el('line', {class: 'history-line', x1: x0, x2: x0, y1: 4, y2: m.top + h}, svg);
                    const start = el('text', {class: 'history-label', x: x0 + 6, y: m.top + 14}, svg);
                    start.textContent = t('Start of history');
                }
                if (d < loadedFrom) {        // not loaded yet: a quiet placeholder band
                    el('rect', {class: 'day-pending', x: x0, y: m.top, width: perDay * unit, height: h}, svg);
                }
                for (let hr = 0; hr < 24; hr += everyH) {
                    const t = el('text', {class: 'axis-text', x: x(d * perDay + hr * (60 / step)), y: height - 6,
                        'text-anchor': hr ? 'middle' : 'start'}, svg);
                    t.textContent = `${pad(hr)}:00`;
                }
            }
        } else {
            // a range: dates where days begin, as many as fit
            const nDays = Math.ceil(total / perDay);
            const every = Math.max(1, Math.ceil(nDays / Math.max(2, Math.floor(viewW / 70))));
            for (let d = 0; d < nDays; d += every) {
                const day = new Date(firstDay.getFullYear(), firstDay.getMonth(), firstDay.getDate() + d);
                const t = el('text', {class: 'axis-text', x: x(d * perDay), y: height - 6,
                    'text-anchor': d ? 'middle' : 'start'}, svg);
                t.textContent = dayMonth(day);
            }
        }

        const ends = [];
        SERIES.forEach((s) => {
            const pts = data.map((d, i) => `${x(slots[i])},${y(d[s.key])}`);
            if (s.area) {
                el('path', {d: `M${x(slots[0])},${y(0)} L${pts.join(' L')} L${x(slots[slots.length - 1])},${y(0)} Z`,
                    fill: color(s), 'fill-opacity': '.1', stroke: 'none'}, svg);
            }
            el('polyline', {points: pts.join(' '), fill: 'none', stroke: color(s), 'stroke-width': 2,
                'stroke-linejoin': 'round', 'stroke-linecap': 'round'}, svg);
            const last = data[data.length - 1][s.key];
            el('circle', {cx: x(slots[slots.length - 1]), cy: y(last), r: 4, fill: color(s), stroke: surface,
                'stroke-width': 2}, svg);
            ends.push({value: last, y: y(last)});
        });
        const apart = ends.every((a, i) => ends.every((b, j) => i === j || Math.abs(a.y - b.y) >= 16));
        if (m.right > 40 && apart) {
            ends.forEach(({value, y: ly}) => {
                const t = el('text', {class: 'end-label', x: x(slots[slots.length - 1]) + 10, y: ly + 4}, svg);
                t.textContent = value.toLocaleString(adminLang);
            });
        }

        // the value axis stays put while the days scroll under it
        const yAxis = el('svg', {class: 'chart-y', viewBox: `0 0 ${m.left} ${height}`, width: m.left, height,
            'aria-hidden': 'true'});
        el('rect', {x: 0, y: 0, width: m.left, height, fill: surface}, yAxis);
        for (let v = 0; v <= top; v += yStep) {
            const t = el('text', {class: 'axis-text', x: m.left - 8, y: y(v) + 4, 'text-anchor': 'end'}, yAxis);
            t.textContent = v.toLocaleString(adminLang);
        }

        // hover layer
        const cross = el('line', {class: 'crosshair', y1: m.top, y2: m.top + h, visibility: 'hidden'}, svg);
        const dots = SERIES.map((s) => el('circle', {r: 4, fill: color(s), stroke: surface, 'stroke-width': 2,
            visibility: 'hidden'}, svg));
        const hit = el('rect', {x: m.left - 12, y: 0, width: x(slots[slots.length - 1]) - m.left + 24, height,
            fill: 'transparent'}, svg);
        if (!scroller.isConnected) box.append(scroller);
        box.append(yAxis);

        const tip = document.createElement('div');
        tip.className = 'chart-tooltip';
        tip.hidden = true;
        box.appendChild(tip);

        let active = -1;
        const show = (i) => {
            active = Math.max(0, Math.min(data.length - 1, i));
            const d = data[active];
            const cx = x(slots[active]);
            cross.setAttribute('x1', cx);
            cross.setAttribute('x2', cx);
            cross.setAttribute('visibility', 'visible');
            SERIES.forEach((s, n) => {
                dots[n].setAttribute('cx', cx);
                dots[n].setAttribute('cy', y(d[s.key]));
                dots[n].setAttribute('visibility', 'visible');
            });
            const at = parseAt(d.d);
            const date = document.createElement('p');
            date.className = 'chart-tooltip__date';
            date.textContent = `${weekday(at)}, ${dayMonth(at)} · ${hhmm(at)}–${hhmm(new Date(at.getTime() + (step - 1) * 60000))}`;
            const rows = SERIES.map((s) => {                 // value leads, series name follows
                const row = document.createElement('div');
                row.className = 'chart-tooltip__row';
                const key = document.createElement('span');
                key.className = 'chart-tooltip__key';
                key.style.background = color(s);
                const value = document.createElement('strong');
                value.textContent = d[s.key].toLocaleString(adminLang);
                const name = document.createElement('span');
                name.textContent = s.name;
                row.append(key, value, name);
                return row;
            });
            tip.replaceChildren(date, ...rows);
            tip.hidden = false;
            const half = tip.offsetWidth / 2;
            tip.style.left = `${Math.min(Math.max(cx - scroller.scrollLeft, half), width - half)}px`;
        };
        const hide = () => {
            active = -1;
            tip.hidden = true;
            cross.setAttribute('visibility', 'hidden');
            dots.forEach((dot) => dot.setAttribute('visibility', 'hidden'));
        };
        const nearest = (clientX) => {
            const slot = (clientX - svg.getBoundingClientRect().left - m.left) / unit;
            let best = 0;
            for (let i = 1; i < slots.length; i += 1) {
                if (Math.abs(slots[i] - slot) < Math.abs(slots[best] - slot)) best = i;
            }
            return best;
        };
        hit.addEventListener('pointermove', (e) => show(nearest(e.clientX)));
        hit.addEventListener('pointerdown', (e) => show(nearest(e.clientX)));     // tap on phones
        hit.addEventListener('pointerleave', (e) => { if (e.pointerType !== 'touch') hide(); });   // touch: stays until the next tap
        svg.addEventListener('focus', () => show(active < 0 ? data.length - 1 : active));
        svg.addEventListener('blur', hide);
        svg.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                e.preventDefault();
                show((active < 0 ? data.length - 1 : active) + (e.key === 'ArrowLeft' ? -1 : 1));
            } else if (e.key === 'Home' || e.key === 'End') {
                e.preventDefault();
                show(e.key === 'Home' ? 0 : data.length - 1);
            } else if (e.key === 'Escape') {
                hide();
            }
        });

        // first draw: the newest day in view; later the scroll position is left alone (a finger may be on it),
        // except when the axis grew at its left end — then the same spot is kept where it was
        if (fromRight == null && box.chartRestore != null) {           // re-rendered: back where it was
            scroller.scrollLeft = scroller.scrollWidth - box.chartRestore;
            box.chartRestore = null;
        } else if (fromRight == null) scroller.scrollLeft = scroller.scrollWidth;
        else if (scroller.scrollWidth - scroller.scrollLeft !== fromRight) scroller.scrollLeft = scroller.scrollWidth - fromRight;
        box.chartView = {oneDay, unit, perDay, loadedFrom, m, atHistoryStart, historyDays, top, viewW, slots, SERIES};
        if (oneDay && box.dataset.dayUrl && !scroller.dataset.wired) {
            scroller.dataset.wired = '1';
            let frame = 0;
            scroller.addEventListener('scroll', () => {
                box.querySelector('.chart-tooltip')?.setAttribute('hidden', '');
                needEarlier(box);
                cancelAnimationFrame(frame);
                frame = requestAnimationFrame(() => rescale(box));
            }, {passive: true});
        }
        if (oneDay) {
            needEarlier(box);
            requestAnimationFrame(() => rescale(box));   // first draw: fit the scale to the days now on screen
        }
    }

    // scrolled to quieter or busier days: redraw when the visible part needs another value scale
    function rescale(box) {
        const view = box.chartView;
        const scroller = box.querySelector('.chart-scroll');
        const data = box.chartState?.[CHART_SERIES[box.dataset.metric] ? box.dataset.metric : 'views'];
        if (!view?.oneDay || !scroller || !data?.length) return;
        const from = (scroller.scrollLeft - view.m.left) / view.unit - 1;
        const to = from + view.viewW / view.unit + 2;
        const seen = data.filter((p, i) => view.slots[i] >= from && view.slots[i] <= to);
        const max = Math.max(0, ...(seen.length ? seen : data).flatMap((d) => view.SERIES.map((s) => d[s.key] ?? 0)));
        if (niceScale(max).top !== view.top) drawChart(box);
    }

    // load the day before the first loaded one while it's (nearly) in view; past the grid, widen it
    function needEarlier(box) {
        const view = box.chartView;
        const scroller = box.querySelector('.chart-scroll');
        if (!view?.oneDay || !scroller) return;
        const dayW = view.perDay * view.unit;
        const leftDay = (scroller.scrollLeft - view.m.left) / dayW;            // the day at the left edge
        if (view.loadedFrom > 0 && leftDay - view.loadedFrom < 1.5) loadEarlierDay(box);   // loadedFrom 0: the grid's first day is in
        if (leftDay < 2 && !view.atHistoryStart && box.chartState.span < MAX_DAYS_BACK) {   // near the grid's start: more room
            box.chartState.span = Math.min(MAX_DAYS_BACK, view.historyDays, box.chartState.span + DAYS_AHEAD_OF_SCROLL);
            drawChart(box);
        }
    }

    // the one-day chart: the day before the first one shown, from the server (same link filters as the page)
    async function loadEarlierDay(box) {
        const charts = box.chartState;
        if (!charts || box.dataset.loadingDay || charts.noMore) return;
        const first = parseAt(charts.views[0].d);
        const before = new Date(first.getFullYear(), first.getMonth(), first.getDate() - 1);
        const tooOld = charts.history_start && isoDay(before) < charts.history_start;   // nothing recorded that early
        if (tooOld || dayIndex(before, new Date()) > MAX_DAYS_BACK) { charts.noMore = true; return; }
        box.dataset.loadingDay = '1';
        box.classList.add('is-loading-day');
        try {
            const url = new URL(box.dataset.dayUrl, location.href);
            url.searchParams.set('day', isoDay(before));
            const response = await fetch(url, {headers: {Accept: 'application/json'}, credentials: 'same-origin'});
            if (!response.ok) { charts.noMore = true; return; }
            const day = await response.json();
            if (box.chartState !== charts) return;            // the chart was replaced meanwhile
            for (const key of ['views', 'visitors', 'clicks']) charts[key] = [...day[key], ...charts[key]];
            drawChart(box);
        } catch {
            charts.noMore = true;
        } finally {
            delete box.dataset.loadingDay;
            box.classList.remove('is-loading-day');
        }
    }

    // a re-render (live update, a filter) must not throw away the earlier days the one-day chart has loaded,
    // nor where it was scrolled to — as long as it's the same chart (same day mode, same link filters)
    // the same chart = the same filters; how many table rows are shown or a notice don't matter
    const chartKey = (url) => {
        const u = new URL(url, location.href);
        [...u.searchParams.keys()].filter((k) => k.startsWith('take') || k === 'notice' || k === 'slug')
            .forEach((k) => u.searchParams.delete(k));
        u.searchParams.sort();
        return u.search;
    };
    function keepChart(root) {
        const box = root.querySelector('[data-chart]');
        const scroller = box?.querySelector('.chart-scroll');
        if (!box?.chartState || !box.dataset.dayUrl || !scroller) return null;
        return {state: box.chartState, url: chartKey(box.dataset.dayUrl), fromRight: scroller.scrollWidth - scroller.scrollLeft,
                atEnd: scroller.scrollWidth - scroller.scrollLeft - scroller.clientWidth < 4};
    }
    function restoreChart(root, kept) {
        const box = root.querySelector('[data-chart]');
        if (!kept || !box?.dataset.dayUrl || chartKey(box.dataset.dayUrl) !== kept.url) return;
        const fresh = JSON.parse(box.querySelector('[data-chart-data]').textContent || '{}');
        if (fresh.step !== kept.state.step || !fresh.views?.length) return;
        const firstFresh = fresh.views[0].d.slice(0, 10);              // the fresh data starts at this day
        for (const key of ['views', 'visitors', 'clicks']) {
            fresh[key] = [...(kept.state[key] || []).filter((p) => p.d.slice(0, 10) < firstFresh), ...(fresh[key] || [])];
        }
        fresh.span = kept.state.span;
        fresh.noMore = kept.state.noMore;
        box.chartState = fresh;
        box.chartRestore = kept.atEnd ? null : kept.fromRight;         // at the newest end: stay at the end
    }

    function initCharts(root = document) {
        root.querySelectorAll('[data-chart]').forEach((box) => {
            drawChart(box);
            if (!box.dataset.observed) {
                box.dataset.observed = '1';
                let last = box.clientWidth;
                new ResizeObserver(() => {
                    if (Math.abs(box.clientWidth - last) > 4) { last = box.clientWidth; drawChart(box); }
                }).observe(box);
            }
        });
    }

    const phone = window.matchMedia('(max-width: 1100px)');

    /* ---------- blocks that are always open on wide screens, collapsible on phones ---------- */
    // data-open-wide="force": also closed on phones whatever the server rendered (the period sheet)
    function syncWide(root = document) {
        root.querySelectorAll('[data-open-wide]').forEach((d) => {
            if (!phone.matches) d.open = true;
            else if (d.dataset.openWide === 'force') d.open = false;
        });
    }
    syncWide();
    phone.addEventListener('change', () => syncWide());
    document.addEventListener('click', (e) => {
        if (!phone.matches) {                                   // nothing to collapse on wide screens
            if (e.target.closest('[data-open-wide] > summary')) e.preventDefault();
            return;
        }
    });

    /* ---------- custom dates: a panel with two dates, a live day count and quick picks ---------- */
    document.addEventListener('input', (e) => {
        const panel = e.target.closest('[data-period-custom]');
        if (!panel) return;
        const from = panel.querySelector('[data-period-from]');
        const to = panel.querySelector('[data-period-to]');
        to.min = from.value;                       // the end can't come before the start, nor the start after the end
        from.max = to.value;
        const count = panel.querySelector('[data-period-count]');
        const days = from.value && to.value ? Math.round((new Date(to.value) - new Date(from.value)) / 864e5) + 1 : 0;
        count.textContent = days > 0 ? t(days === 1 ? '%(n)s day' : '%(n)s days', {n: days}) : t('The end is before the start');
        count.classList.toggle('is-bad', days <= 0);
    });
    document.addEventListener('click', (e) => {
        const quick = e.target.closest('[data-period-quick]');
        if (quick) {
            const panel = quick.closest('[data-period-custom]');
            const now = new Date();
            const last = quick.dataset.periodQuick === 'last-month';
            const start = new Date(now.getFullYear(), now.getMonth() - (last ? 1 : 0), 1);
            const end = last ? new Date(now.getFullYear(), now.getMonth(), 0) : now;
            const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
            const from = panel.querySelector('[data-period-from]');
            const to = panel.querySelector('[data-period-to]');
            from.max = ''; to.min = '';
            from.value = iso(start);
            to.value = iso(end);
            from.dispatchEvent(new Event('input', {bubbles: true}));
            return;
        }
        if (e.target.closest('[data-period-cancel]')) e.target.closest('[data-period-custom]').open = false;
        const open = document.querySelector('[data-period-custom][open]');   // a click outside closes it
        if (open && !open.contains(e.target)) open.open = false;
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const open = document.querySelector('[data-period-custom][open]');
            if (open) { open.open = false; open.querySelector('summary').focus(); }
        }
    });

    /* ---------- filters, sorting and search: the server renders, we swap the blocks in ---------- */
    const filterForm = document.querySelector('[data-admin-filter]');
    const results = document.querySelector('[data-admin-results]');
    let pending = null;

    /* ---------- animated numbers: after any re-render, changed counts roll to their new value ---------- */
    // tiles, the per-row clicks / uniques and the row counter; each is keyed so the same number is
    // found again in the fresh page (rows by data-key, tiles by position, the counter by its card)
    const NUMBERS = '.admin-tile__value, .admin-table td.cell-stat strong, .card-head .count';
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const numberKey = (el) => {
        const tile = el.closest('.admin-tile');
        if (tile) return `tile:${[...tile.parentElement.children].indexOf(tile)}`;
        const td = el.closest('td');
        if (td) return `row:${td.closest('tr').dataset.key}|${td.dataset.label}`;
        return `count:${el.closest('section')?.id}`;
    };
    const valueOf = (el) => Number(el.textContent.replace(/\D/g, '')) || 0;
    const grouped = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');   // "12 345", as the server writes it
    const snapshotNumbers = () => new Map([...results.querySelectorAll(NUMBERS)].map((el) => [numberKey(el), valueOf(el)]));
    function rollNumbers(before) {
        results.querySelectorAll(NUMBERS).forEach((el) => {
            const from = before.get(numberKey(el));
            const to = valueOf(el);
            if (from === undefined || from === to) return;
            (el.closest('.admin-tile, td') ?? el).classList.add('is-fresh');
            if (reduceMotion.matches) return;
            const started = performance.now();
            const duration = Math.min(900, 450 + Math.abs(to - from) * 8);
            const frame = (now) => {
                const t = Math.min(1, (now - started) / duration);
                const eased = 1 - (1 - t) ** 3;                          // ease-out cubic
                el.textContent = grouped(Math.round(from + (to - from) * eased));
                if (t < 1 && el.isConnected) requestAnimationFrame(frame);
            };
            requestAnimationFrame(frame);
        });
    }

    async function load(url, {push = true, silent = false} = {}) {
        pending?.abort();
        pending = new AbortController();
        const focused = document.activeElement;
        const sameName = (name) => [...document.querySelectorAll(`[name="${CSS.escape(name)}"]:not([type="hidden"])`)];
        const focus = !silent && focused?.name ? {name: focused.name, index: sameName(focused.name).indexOf(focused),
            start: focused.selectionStart, end: focused.selectionEnd} : null;
        const openDetails = [...results.querySelectorAll('details[data-admin-keep-open][open]')].map((d) => d.dataset.adminKeepOpen);
        const scrolls = [...results.querySelectorAll('.admin-table-wrap')].map((w) => w.scrollTop);
        const before = snapshotNumbers();
        if (!silent) results.classList.add('is-loading');      // keep the previous render, dimmed
        try {
            const response = await fetch(url, {headers: {Accept: 'text/html'}, credentials: 'same-origin', signal: pending.signal});
            if (response.redirected && new URL(response.url).pathname !== location.pathname) {
                location.href = response.url;        // session expired → login page
                return;
            }
            const page = new DOMParser().parseFromString(await response.text(), 'text/html');
            const freshResults = page.querySelector('[data-admin-results]');
            const freshFilter = page.querySelector('[data-admin-filter]');
            if (!freshResults || !freshFilter) throw new Error('unexpected response');
            const chartKept = keepChart(results);                 // the one-day chart's earlier days and scroll
            results.replaceChildren(...freshResults.childNodes);
            filterForm.replaceChildren(...freshFilter.childNodes);
            restoreChart(results, chartKept);
            syncWide(filterForm);
            openDetails.forEach((key) => results.querySelector(`details[data-admin-keep-open="${key}"]`)?.setAttribute('open', ''));
            if (push) history.pushState(null, '', url);
            initCharts(results);
            watchMore();
            results.querySelectorAll('.admin-table-wrap').forEach((w, i) => { w.scrollTop = scrolls[i] || 0; });
            rollNumbers(before);
            stamp();
            if (focus) {                              // typing in the search box survives the swap
                // the same name also lives in hidden "keep state" fields of other forms — skip those
                const input = sameName(focus.name)[Math.max(0, focus.index)];   // the same one of several search inputs
                input?.focus({preventScroll: true});
                try {
                    if (input && focus.start != null) input.setSelectionRange(focus.start, focus.end);
                } catch { /* not a text field */ }
            }
        } catch (error) {
            if (error.name === 'AbortError') { if (silent) dirty = true; return; }   // superseded: keep the event
            if (silent) { dirty = true; return; }   // offline for a moment: retried on the next event
            console.error('admin: in-place update failed, loading the page instead', error);
            location.href = url;
        } finally {
            results.classList.remove('is-loading');
        }
    }

    // the live badge is part of the swapped block, so its state is re-applied after every render
    let connected = true;              // optimistic: shown as live unless connecting takes a while
    let updatedAt = null;
    const time = (d) => d.toLocaleTimeString(adminLang, {hour: '2-digit', minute: '2-digit', second: '2-digit'});
    function stamp(fresh = true) {
        if (fresh) updatedAt = new Date();
        const badge = results?.querySelector('[data-admin-live]');
        if (!badge) return;
        badge.classList.toggle('is-on', connected);
        badge.textContent = !connected ? t('Connecting…') : updatedAt ? t('Live · updated %(time)s', {time: time(updatedAt)}) : t('Live');
    }

    /* ---------- live data: the server pushes "new data" over a WebSocket, the page re-renders in place ---------- */
    const busy = () => document.hidden
        || results.classList.contains('is-loading')
        || document.querySelector('.table-popover:popover-open, dialog[open], .mobile-menu.is-open')
        || phone.matches && document.querySelector('.period__custom[open]')
        || document.activeElement?.matches?.('input, select, textarea')
        || results.querySelector('.chart:hover .chart-tooltip:not([hidden])');   // mouse reading the chart (a tapped tooltip may go)
    let dirty = false;           // an event arrived that the page hasn't shown yet
    let refreshTimer = null;
    async function refresh() {
        refreshTimer = null;
        if (!dirty) return;
        if (busy()) { refreshTimer = setTimeout(refresh, 1000); return; }   // don't pull the page from under the user
        dirty = false;
        await load(feedUrl(), {push: false, silent: true});   // changed numbers roll in load()
    }
    const PAGE_SIZE = 30;   // app/admin_tables.py PAGE_SIZE
    // a feed scrolled past its first page is re-read from the top with as many rows as are shown
    function feedUrl() {
        const url = new URL(location.href);
        results.querySelectorAll('table[data-take-param]').forEach((table) => {
            const shown = table.tBodies[0]?.rows.length ?? 0;
            if (shown > PAGE_SIZE) url.searchParams.set(table.dataset.takeParam, String(shown));
        });
        return url.href;
    }

    /* ---------- cursor-paginated feeds: "Show more" appends the next page; it also loads itself on scroll ---------- */
    let loadingMore = false;
    async function loadMore(link) {
        if (loadingMore) return;
        loadingMore = true;
        const box = link.closest('[data-load-more]');
        box.classList.add('is-loading');
        try {
            const response = await fetch(link.href, {headers: {Accept: 'text/html'}, credentials: 'same-origin'});
            const page = new DOMParser().parseFromString(await response.text(), 'text/html');
            const table = box.closest('section').querySelector('table[data-take-param]');
            const fresh = page.querySelector(`table[data-take-param="${table.dataset.takeParam}"]`);
            if (!fresh) throw new Error('unexpected response');
            table.tBodies[0].append(...fresh.tBodies[0].rows);
            const shown = table.tBodies[0].rows.length;
            const next = fresh.closest('section').querySelector('[data-load-more]');
            if (next) {
                next.querySelector('.load-more__count').textContent = t('%(shown)s of %(total)s', {shown, total: next.dataset.total});
                box.replaceWith(next);
            } else {
                box.remove();
            }
            showShare();
            watchMore();
        } catch (error) {
            console.error('admin: could not load more rows', error);
            box.classList.remove('is-loading');
        } finally {
            loadingMore = false;
        }
    }
    document.addEventListener('click', (e) => {
        const link = e.target.closest('[data-admin-more]');
        if (!link || e.metaKey || e.ctrlKey) return;
        e.preventDefault();
        loadMore(link);
    });
    const moreObserver = 'IntersectionObserver' in window ? new IntersectionObserver((entries) => {
        entries.forEach((entry) => { if (entry.isIntersecting) loadMore(entry.target); });
    }, {rootMargin: '400px 0px'}) : null;
    function watchMore() {
        moreObserver?.disconnect();
        document.querySelectorAll('[data-admin-more]').forEach((link) => moreObserver?.observe(link));
    }

    const schedule = (delay = 600) => {   // a burst of clicks becomes one re-render
        dirty = true;
        if (!refreshTimer) refreshTimer = setTimeout(refresh, delay);
    };

    function connect(attempt = 0) {
        const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/admin/ws`);
        const slow = setTimeout(() => { connected = false; stamp(false); }, 1500);   // say so only if it takes a while
        socket.addEventListener('open', () => {
            clearTimeout(slow);
            const reconnect = attempt > 0;
            attempt = 0;
            connected = true;
            stamp(!reconnect);
            if (reconnect) schedule(0);              // catch up on whatever happened while offline
        });
        // a page lists the kinds it shows in data-live (the Tests pages: attempts only); none listed = all
        const wanted = results.dataset.live?.split(' ');
        socket.addEventListener('message', (e) => {
            let kind = '';
            try { kind = JSON.parse(e.data).type; } catch { /* not ours */ }
            if (!wanted || wanted.includes(kind)) schedule();
        });
        socket.addEventListener('close', (e) => {
            clearTimeout(slow);
            connected = false;
            stamp(false);
            if (e.code === 1008) { location.reload(); return; }   // session gone → the page redirects to login
            const delay = Math.min(30000, 1000 * 2 ** attempt) * (0.75 + Math.random() / 2);
            setTimeout(() => connect(attempt + 1), delay);
        });
    }

    const urlOf = (form) => {
        const params = new URLSearchParams();
        for (const [k, v] of new FormData(form)) if (v !== '') params.append(k, v);
        return `${form.action}?${params}`;
    };

    if (filterForm && results && window.fetch && window.DOMParser) {
        // sort headers, presets and "reset" links
        document.addEventListener('click', (e) => {
            const link = e.target.closest('[data-admin-sort], [data-admin-preset]');
            if (!link || e.metaKey || e.ctrlKey || e.shiftKey) return;
            e.preventDefault();
            load(link.href);
        });
        // any admin form (date range, table filters)
        document.addEventListener('submit', (e) => {
            const form = e.target.closest('[data-admin-form]');
            if (!form) return;
            e.preventDefault();
            if (!form.reportValidity()) return;
            document.querySelectorAll('.table-popover:popover-open').forEach((p) => p.hidePopover());
            load(urlOf(form));
        });
        document.addEventListener('change', (e) => {
            if (!phone.matches && e.target.matches('[data-admin-filter] input[type="date"]') && filterForm.checkValidity()) filterForm.requestSubmit();
        });
        // search as you type (debounced); Enter submits right away
        let typing = null;
        document.addEventListener('input', (e) => {
            if (!e.target.matches('[data-admin-search]')) return;
            clearTimeout(typing);
            typing = setTimeout(() => e.target.form.requestSubmit(), 400);
        });
        stamp(false);
        if ('WebSocket' in window) connect();
        document.addEventListener('visibilitychange', () => { if (!document.hidden && dirty) schedule(0); });
        window.addEventListener('popstate', () => load(location.href, {push: false}));
    }


    /* ---------- table popovers: anchor under their button, "all / none" helpers ---------- */
    document.addEventListener('toggle', (e) => {
        const popover = e.target;
        if (!popover.matches?.('.table-popover')) return;
        const button = document.querySelector(`[popovertarget="${popover.id}"]`);
        button?.setAttribute('aria-expanded', String(e.newState === 'open'));
        if (e.newState !== 'open' || !button) return;
        const focusFirst = () => popover.querySelector('input, .menu-item:not([hidden]), .segmented__item')?.focus({preventScroll: true});
        if (phone.matches) {                   // bottom sheet — CSS places it
            popover.style.cssText = '';
            focusFirst();
            return;
        }
        const b = button.getBoundingClientRect();
        const width = popover.offsetWidth;
        const left = Math.min(Math.max(8, b.left), window.innerWidth - width - 8);
        const below = b.bottom + 6;
        const fitsBelow = below + popover.offsetHeight < window.innerHeight - 8;
        popover.style.position = 'fixed';
        popover.style.left = `${left}px`;
        popover.style.top = `${fitsBelow ? below : Math.max(8, b.top - popover.offsetHeight - 6)}px`;
        focusFirst();
    }, true);
    document.addEventListener('click', (e) => {
        const tool = e.target.closest('.table-popover [data-check]');
        if (!tool) return;
        tool.closest('.table-popover').querySelectorAll('input[type="checkbox"]')
            .forEach((box) => { box.checked = tool.dataset.check === 'all'; });
    });
    // the popover is anchored with fixed coordinates: close it when the page or the table scrolls under it
    window.addEventListener('scroll', (e) => {
        if (phone.matches || (e.target instanceof Element && e.target.closest('.table-popover'))) return;
        document.querySelectorAll('.table-popover:popover-open').forEach((p) => p.hidePopover());
    }, {passive: true, capture: true});

    /* ---------- toast: result of the last action, copy feedback ---------- */
    const toast = document.querySelector('[data-toast]');
    let toastTimer = null;
    function showToast(text) {
        if (!toast) return;
        if (text) toast.querySelector('[data-toast-text]').textContent = text;
        toast.classList.add('is-visible');
        clearTimeout(toastTimer);
        const hasAction = !text && toast.querySelector('.toast__action');
        toastTimer = setTimeout(() => toast.classList.remove('is-visible'), hasAction ? 7000 : 3500);
    }
    if (toast?.hasAttribute('data-show-on-load')) {
        showToast();
        document.querySelector('.is-highlight')?.scrollIntoView({block: 'center'});
        const url = new URL(location.href);        // a reload must not repeat the notice
        url.searchParams.delete('notice');
        url.searchParams.delete('slug');
        history.replaceState(null, '', url);
    }

    /* ---------- copy & share ---------- */
    // what the toast says: short and human, never a raw tracking URL
    function copiedText(url) {
        const {pathname} = new URL(url, location.href);
        if (pathname.startsWith('/l/')) return t('Copied %(path)s', {path: pathname});
        if (pathname.startsWith('/go/')) return t('Tracked link copied');
        return t('Link copied');
    }
    const morph = (el, name) => window.morphIcon?.(el, name);
    document.addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-copy]');
        if (!btn) return;
        try { await navigator.clipboard.writeText(btn.dataset.copy); } catch { return; }
        const label = btn.querySelector('.act-btn__text');
        btn.classList.add('is-done');
        if (label) label.textContent = t('Copied');
        morph(btn, 'check');
        toast?.querySelector('.toast__action')?.remove();   // the action is done; the toast just confirms
        showToast(copiedText(btn.dataset.copy));
        setTimeout(() => {
            btn.classList.remove('is-done');
            if (label) label.textContent = t('Copy');
            morph(btn, 'copy');
        }, 1600);
    });
    const showShare = () => {
        if (navigator.share) document.querySelectorAll('[data-share][hidden]').forEach((b) => { b.hidden = false; });
    };
    showShare();
    document.addEventListener('click', (e) => {
        const share = e.target.closest('[data-share]');
        if (!share) return;
        share.closest('[popover]')?.hidePopover();
        navigator.share({url: share.dataset.share, title: share.dataset.title || undefined}).catch(() => {});
    });

    /* ---------- dialogs: create / edit a short link, confirm delete ---------- */
    const linkDialog = document.getElementById('link-dialog');
    const rowData = (el) => JSON.parse(el.closest('[data-link]').dataset.link);
    let returnFocus = null;                             // where focus goes back when a dialog closes
    const openerOf = (el) => {
        const popover = el.closest('[popover]');
        popover?.hidePopover();
        return popover ? document.querySelector(`[popovertarget="${CSS.escape(popover.id)}"]`) : el;
    };
    const showDialog = (dialog, opener) => {
        returnFocus = opener;
        dialog.querySelectorAll('[type="submit"]').forEach((b) => { b.disabled = false; });
        dialog.showModal();
    };
    document.querySelectorAll('dialog.sheet').forEach((d) => d.addEventListener('close', () => {
        returnFocus?.isConnected ? returnFocus.focus() : document.querySelector('[data-link-new]')?.focus();
        returnFocus = null;
    }));
    const setMode = (dialog, mode) => {
        dialog.querySelectorAll('[data-new][data-edit]').forEach((node) => { node.textContent = node.dataset[mode]; });
        dialog.querySelectorAll('[data-edit-only]').forEach((node) => { node.hidden = mode !== 'edit'; });
    };
    function openLinkDialog(link = null, opener = null) {
        const form = linkDialog.querySelector('form');
        form.querySelector('.admin-alert')?.remove();
        form.action = link ? link.action : linkDialog.dataset.createAction;
        form.target_url.value = link?.target_url ?? '';
        form.slug.value = link?.slug ?? '';
        form.title.value = link?.title ?? '';
        form.active.checked = link ? link.active : true;
        setMode(linkDialog, link ? 'edit' : 'new');
        showDialog(linkDialog, opener);
        if (link && phone.matches) linkDialog.querySelector('.sheet__title').focus();
        else form.target_url.focus();
    }
    document.addEventListener('click', (e) => {
        const target = e.target;
        if (target.closest('[data-link-new]')) openLinkDialog(null, target.closest('[data-link-new]'));
        else if (target.closest('[data-link-edit]')) {
            const link = rowData(target);
            openLinkDialog(link, openerOf(target));
        } else if (target.closest('[data-link-delete]')) {
            const link = rowData(target);
            window.adminConfirm({
                title: t('Delete /l/%(slug)s?', {slug: link.slug}), danger: true,
                message: t('The address will stop working. The clicks it already had stay in the analytics.'),
            }).then((yes) => {
                if (!yes) return;
                const form = Object.assign(document.createElement('form'), {method: 'post', action: link.delete, hidden: true});
                document.body.append(form);
                form.submit();
            });
        } else if (target.closest('[data-dialog-close]')) {
            target.closest('dialog')?.close();
        } else if (target.matches('dialog.sheet:not(.adc)')) {
            target.close();                             // tap on the backdrop (the confirm dialog, .adc, decides about its own backdrop)
        }
    });
    if (linkDialog?.hasAttribute('data-open-on-load')) {   // server validation error: show it where it happened
        history.replaceState(null, '', linkDialog.dataset.pageUrl);   // the address is the page, not the POST URL
        showDialog(linkDialog, null);
        linkDialog.querySelector('input:not([type="hidden"])')?.focus();
    }
    document.addEventListener('submit', (e) => {
        const form = e.target;
        if (form.method !== 'post' || e.defaultPrevented) return;
        // the page's period / sort / search ride along, so the server returns to the same view
        const action = new URL(form.action, location.href);
        if (!action.search && location.search) {
            const own = form.getAttribute('action');
            form.action = action.pathname + location.search;
            setTimeout(() => { if (own === null) form.removeAttribute('action'); else form.setAttribute('action', own); });   // the form keeps its own action
        }
        if (form.closest('dialog.sheet')) form.querySelector('[type="submit"]')?.setAttribute('disabled', '');   // no double submit
    });

    /* ---------- several searches: one input per link ---------- */
    const syncRemove = (box) => {
        const rows = box.querySelectorAll('.multi-search__row');
        rows.forEach((row) => { row.querySelector('[data-search-remove]').hidden = rows.length < 2; });
    };
    document.addEventListener('click', (e) => {
        const add = e.target.closest('[data-search-add]');
        if (add) {
            const box = add.closest('[data-multi-search]');
            const row = box.querySelector('.multi-search__row').cloneNode(true);
            const input = row.querySelector('input');
            input.value = '';
            box.insertBefore(row, add);
            syncRemove(box);
            input.focus();
            return;
        }
        const remove = e.target.closest('[data-search-remove]');
        if (remove) {
            const box = remove.closest('[data-multi-search]');
            const form = remove.closest('.multi-search__row').querySelector('input').form;
            const hadText = remove.closest('.multi-search__row').querySelector('input').value.trim() !== '';
            remove.closest('.multi-search__row').remove();
            syncRemove(box);
            if (hadText) form?.requestSubmit();            // an empty one leaves the results as they are
        }
    });

    /* ---------- tiles pick the chart: instant switch, remembered for the next visit ---------- */
    document.addEventListener('click', (e) => {
        const tile = e.target.closest('[data-chart-pick]');
        if (!tile) return;
        const metric = tile.dataset.chartPick;
        const box = document.querySelector('[data-chart]');
        if (!box || box.dataset.metric === metric) return;
        document.querySelectorAll('[data-chart-pick]').forEach((t) => t.setAttribute('aria-pressed', String(t === tile)));
        document.querySelectorAll('[data-chart-for]').forEach((node) => { node.hidden = node.dataset.chartFor !== metric; });
        box.dataset.metric = metric;
        drawChart(box);
        document.cookie = `admin_chart=${metric}; path=/admin; max-age=31536000; samesite=lax`;   // live refreshes keep it too
    });

    /* ---------- a folding block remembers whether it was left open (the server renders it so next time) ---------- */
    document.addEventListener('toggle', (e) => {
        const name = e.target.dataset?.remember;
        if (name) document.cookie = `${name}=${e.target.open ? 1 : 0}; path=/admin; max-age=31536000; samesite=lax`;
    }, true);   // toggle doesn't bubble

    /* ---------- favorites: the star toggles in place (optimistic), the server confirms ---------- */
    document.addEventListener('submit', async (e) => {
        const form = e.target.closest('[data-star]');
        if (!form) return;
        e.preventDefault();
        const btn = form.querySelector('.star');
        const on = !btn.classList.contains('is-on');
        const paint = (state) => {
            btn.classList.toggle('is-on', state);
            btn.setAttribute('aria-pressed', String(state));
            btn.title = state ? t('Remove from favorites') : t('Add to favorites');
        };
        paint(on);
        btn.classList.add('is-popping');
        setTimeout(() => btn.classList.remove('is-popping'), 300);
        try {
            const response = await fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin',
                headers: {Accept: 'application/json'}});
            if (!response.ok) throw new Error(String(response.status));
            paint((await response.json()).favorite);
            showToast(on ? t('Added to favorites') : t('Removed from favorites'));
        } catch {
            paint(!on);
            showToast(t('Could not change the favorite. Try again.'));
        }
    }, true);

    // a form that asks first (<form data-confirm="Sure?">) is handled by admin-confirm.js

    /* ---------- course chart: a tooltip over the day under the pointer or focus (works after live re-renders) ---------- */
    (() => {
        // English has two forms, Russian three; the course page gives the words of each series (data-noun-a="one,few,many"), these are the fallbacks
        const noun = (n, one, few, many) => {
            if (adminLang !== 'ru') return n === 1 ? one : many;
            const m = n % 100, d = n % 10;
            return m > 10 && m < 15 ? many : d === 1 ? one : d > 1 && d < 5 ? few : many;
        };
        const show = (col) => {
            const plot = col.closest('[data-course-chart]'), tip = plot.querySelector('.course-plot__tip');
            plot.querySelectorAll('.course-plot__col.is-on').forEach((c) => c.classList.remove('is-on'));
            col.classList.add('is-on');
            const p = Number(col.dataset.people), st = Number(col.dataset.steps);
            // the words of each series come from the chart (data-noun-a="one,few,many", data-suffix-a); the course page keeps its own
            const words = (key, fallback) => (plot.dataset[`noun${key}`] || fallback).split(',');
            const [a1, a2, a5] = words('A', t('person,people,people')), [b1, b2, b5] = words('B', t('step,steps,steps'));
            const suffixB = 'suffixB' in plot.dataset ? plot.dataset.suffixB : t('done');
            const suffixA = plot.dataset.suffixA || '';
            tip.innerHTML = `<strong>${col.dataset.when}</strong>`
                + `<span><i class="course-legend__dot"></i>${p} ${noun(p, a1, a2, a5)}${suffixA ? ' ' + suffixA : ''}</span>`
                + `<span><i class="course-legend__dot course-legend__dot--steps"></i>${st} ${noun(st, b1, b2, b5)}${suffixB ? ' ' + suffixB : ''}</span>`;
            tip.hidden = false;
            const box = plot.getBoundingClientRect(), c = col.getBoundingClientRect();
            const left = c.left - box.left + c.width / 2 - tip.offsetWidth / 2;
            tip.style.left = `${Math.max(0, Math.min(left, box.width - tip.offsetWidth))}px`;
            tip.style.top = `${Math.max(0, c.top - box.top - tip.offsetHeight - 6)}px`;
        };
        const hide = (plot) => { plot.querySelector('.course-plot__tip').hidden = true; plot.querySelectorAll('.is-on').forEach((c) => c.classList.remove('is-on')); };
        document.addEventListener('pointerover', (e) => { const c = e.target.closest('.course-plot__col'); if (c) show(c); });
        document.addEventListener('pointerout', (e) => { const plot = e.target.closest('[data-course-chart]'); if (plot && !plot.contains(e.relatedTarget)) hide(plot); });
        document.addEventListener('focusin', (e) => { const c = e.target.closest('.course-plot__col'); if (c) show(c); });
        document.addEventListener('focusout', (e) => { const plot = e.target.closest('[data-course-chart]'); if (plot) hide(plot); });
    })();

    initCharts();
    watchMore();
})();
