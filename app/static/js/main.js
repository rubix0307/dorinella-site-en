(function () {
    'use strict';

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* ---------- Header state on scroll ---------- */
    const header = document.querySelector('[data-header]');
    if (header) {
        const onScroll = () => header.classList.toggle('is-scrolled', window.scrollY > 8);
        requestAnimationFrame(onScroll);   // not at once: reading scrollY now would force a layout
        window.addEventListener('scroll', onScroll, {passive: true});
    }

    /* ---------- Mobile menu ---------- */
    const menu = document.querySelector('[data-menu]');
    const toggle = document.querySelector('[data-menu-toggle]');
    if (menu && toggle) {
        // everything behind the full-screen overlay leaves the tab order / a11y tree while it's open
        const background = document.querySelectorAll('main, footer, .skip-link, .site-nav, .site-header__cta');
        const setOpen = (open) => {
            background.forEach((el) => { el.inert = open; });
            toggle.setAttribute('aria-expanded', String(open));
            toggle.setAttribute('aria-label', open ? 'Close the menu' : 'Open the menu');
            menu.classList.toggle('is-open', open);
            menu.setAttribute('aria-hidden', String(!open));
            document.body.classList.toggle('is-locked', open);
            document.dispatchEvent(new CustomEvent('menu:change', {detail: {open}}));
            if (open) menu.querySelector('a')?.focus({preventScroll: true, focusVisible: false});   // focus for keyboards, no ring on a tap
        };
        menu.setAttribute('aria-hidden', 'true');
        menu.removeAttribute('hidden');
        // order of the staggered entrance (links first, then the social buttons)
        menu.querySelectorAll('.mobile-menu__nav a, .mobile-menu__social .btn').forEach((el, i) => {
            el.style.setProperty('--i', String(i));
        });

        toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
        menu.addEventListener('click', (e) => {
            if (e.target.closest('a')) setOpen(false);
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && menu.classList.contains('is-open')) {
                setOpen(false);
                toggle.focus();
            }
        });
        window.matchMedia('(min-width: 961px)').addEventListener('change', (e) => {
            if (e.matches) setOpen(false);
        });
    }

    /* ---------- Reveal on scroll ---------- */
    const revealItems = document.querySelectorAll('[data-reveal]');
    if ('IntersectionObserver' in window && !reduceMotion) {
        document.documentElement.classList.add('reveal-ready');
        // stagger siblings that share a parent
        revealItems.forEach((el) => {
            if (el.dataset.reveal === 'quick') return;   // long-form blocks one under another: no stagger
            const siblings = Array.from(el.parentElement.children).filter((c) => c.hasAttribute('data-reveal'));
            const index = siblings.indexOf(el);
            if (index > 0) el.style.setProperty('--reveal-delay', `${Math.min(index, 6) * 0.08}s`);
        });
        const observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('is-visible');
                    observer.unobserve(entry.target);
                }
            });
        }, {rootMargin: '0px 0px -8% 0px', threshold: 0.08});
        // data-reveal="quick" (text blocks, often taller than the screen): start as soon as the top edge shows
        const quickObserver = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('is-visible');
                    quickObserver.unobserve(entry.target);
                }
            });
        }, {threshold: 0});
        revealItems.forEach((el) => (el.dataset.reveal === 'quick' ? quickObserver : observer).observe(el));

        // Anchor jumps: show the target section right away, so it is already in place when the scroll lands
        const revealSection = (hash) => {
            const target = hash.length > 1 ? document.getElementById(decodeURIComponent(hash.slice(1))) : null;
            target?.querySelectorAll('[data-reveal]').forEach((el) => {
                el.classList.add('is-visible');
                observer.unobserve(el);
            });
        };
        revealSection(location.hash);
        document.addEventListener('click', (e) => {
            const link = e.target.closest('a[href*="#"]');
            if (link && link.pathname === location.pathname && link.origin === location.origin) revealSection(link.hash);
        });
    } else {
        revealItems.forEach((el) => el.classList.add('is-visible'));
    }

    /* ---------- Smooth <details> accordion ---------- */
    // The whole <details> height is animated (summary height ↔ full height), so paddings
    // of the content never leave a leftover gap that snaps away at the end.
    // Fires `accordion:change` as soon as the target state is known, so the icon morph
    // (morph.js) starts together with the height animation.
    const ACCORDION_TIMING = {duration: 450, easing: 'cubic-bezier(.22,.61,.36,1)', fill: 'forwards'};

    document.querySelectorAll('[data-accordion]').forEach((details) => {
        const summary = details.querySelector('summary');
        const content = details.querySelector('.accordion__content');
        if (!summary || !content) return;
        let expanded = details.open;
        let heightAnim = null;
        let fadeAnim = null;

        const setExpanded = (value) => {
            expanded = value;
            details.classList.toggle('is-expanded', value);
            details.dispatchEvent(new CustomEvent('accordion:change', {detail: {open: value}}));
        };

        // fractional sizes: offsetHeight rounds and would leave a sub-pixel snap at the end
        const height = (el) => el.getBoundingClientRect().height;
        const closedHeight = () => {
            const style = getComputedStyle(details);
            return height(summary) + parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
        };

        const stop = () => {
            heightAnim?.cancel();
            fadeAnim?.cancel();
            heightAnim = fadeAnim = null;
        };

        const animate = (from, to, opacity, onFinish) => {
            details.style.overflow = 'hidden';
            heightAnim = details.animate({height: [`${from}px`, `${to}px`]}, ACCORDION_TIMING);
            fadeAnim = content.animate({opacity}, ACCORDION_TIMING);
            heightAnim.onfinish = () => {
                onFinish?.();
                stop();  // drop the forwards-fill only after the final state is applied
                details.style.overflow = '';
            };
        };

        details.addEventListener('toggle', () => {
            if (!heightAnim && details.open !== expanded) setExpanded(details.open);
        });

        summary.addEventListener('click', (e) => {
            e.preventDefault();
            setExpanded(!expanded);
            if (reduceMotion) {
                details.open = expanded;
                return;
            }
            const from = height(details);  // current (possibly mid-animation) height
            stop();
            details.open = true;
            const closed = closedHeight();
            const full = height(details);
            const progress = Math.min(1, Math.max(0, (from - closed) / (full - closed || 1)));
            if (expanded) {
                animate(from, full, [progress, 1]);
            } else {
                animate(from, closed, [progress, 0], () => { details.open = false; });
            }
        });
    });

    /* ---------- Self-test: one question at a time, result rendered by the server ---------- */
    document.querySelectorAll('[data-quiz]').forEach((form) => {
        const steps = Array.from(form.querySelectorAll('[data-quiz-step]'));
        const bar = form.querySelector('[data-quiz-bar]');
        const count = form.querySelector('[data-quiz-count]');
        const back = form.querySelector('[data-quiz-back]');
        const submit = form.querySelector('[data-quiz-submit]');
        const next = form.querySelector('[data-quiz-next]');
        const stage = form.querySelector('[data-quiz-stage]');
        const actions = form.querySelector('.quiz__actions');
        let current = 0;

        // Card swap: the old card slides out and fades, the new one slides in from the other side
        // (direction follows “Next” / “Back”), and the stage eases between the two cards' heights.
        let running = [];
        let leaving = null;
        const settle = () => {
            running.forEach((a) => a.cancel());
            running = [];
            if (leaving) {
                leaving.classList.remove('is-leaving');
                leaving.inert = false;
                leaving = null;
            }
        };
        const swap = (from, to, dir, fromHeight) => {
            const toHeight = stage.offsetHeight;
            leaving = from;
            from.classList.add('is-leaving');
            from.inert = true;
            const shift = 56 * dir;
            const ease = 'cubic-bezier(.16, 1, .3, 1)';
            running = [
                from.animate([
                    {opacity: 1, transform: 'none'},
                    {opacity: 0, transform: `translateX(${-shift}px) scale(.96)`},
                ], {duration: 260, easing: 'cubic-bezier(.4, 0, 1, 1)', fill: 'forwards'}),
                to.animate([
                    {opacity: 0, transform: `translateX(${shift}px) scale(.96)`},
                    {opacity: 1, transform: 'none'},
                ], {duration: 520, delay: 90, easing: ease, fill: 'backwards'}),
                stage.animate([{height: `${fromHeight}px`}, {height: `${toHeight}px`}], {duration: 520, easing: ease}),
            ];
            running[0].finished.then(() => {
                if (leaving !== from) return;
                from.classList.remove('is-leaving');
                from.inert = false;
                leaving = null;
            }, () => {});
        };

        // The result (and, on “Take it again”, the form) takes the place of what was there, starting at the
        // same top edge. Scroll only when that edge is out of sight: under the sticky header / above the
        // screen, or so low that most of the block is below it. If the person is looking at it, stay put.
        const bringIntoView = (el) => {
            const headerBottom = document.querySelector('[data-header]')?.getBoundingClientRect().bottom ?? 0;
            const top = el.getBoundingClientRect().top;
            if (top >= headerBottom && top <= window.innerHeight / 2) return;
            el.scrollIntoView({behavior: reduceMotion ? 'auto' : 'smooth', block: 'start'});
        };

        const answered = (i) => steps[i].querySelector('input:checked') !== null;
        let advance = null;   // pending auto-advance after a tap: the card turns by itself, so no “Next” meanwhile
        const cancelAdvance = () => {
            clearTimeout(advance);
            advance = null;
        };
        const show = (i) => {
            const previous = current;
            current = Math.max(0, Math.min(i, steps.length - 1));
            const animate = previous !== current && !reduceMotion && form.classList.contains('is-stepper');
            let fromHeight = 0;
            if (animate) {
                settle();   // a quick double tap: finish the swap in flight before starting the next one
                fromHeight = stage.offsetHeight;
            }
            steps.forEach((step, n) => step.classList.toggle('is-current', n === current));
            if (animate) swap(steps[previous], steps[current], current > previous ? 1 : -1, fromHeight);
            const done = steps.filter((_, n) => answered(n)).length;
            bar.style.width = `${(done / steps.length) * 100}%`;
            count.textContent = `${current + 1} / ${steps.length}`;
            back.classList.toggle('is-shown', current > 0);   // folds away on the first card
            back.inert = current === 0;
            // “Next” only when it is the way on: the card is answered and isn't about to turn by itself
            // (that is a keyboard answer, or a card answered earlier and come back to); on the last card
            // the way on is “See my result”, once that card is answered
            next.hidden = current === steps.length - 1 || !answered(current) || advance !== null;
            form.classList.toggle('is-ready', current === steps.length - 1 && answered(current));
            // the row under the card is only there when it holds a way on — no empty band otherwise
            actions.hidden = next.hidden && !form.classList.contains('is-ready');
        };

        form.classList.add('is-stepper');
        back.hidden = false;   // the no-JS form has no use for it
        show(0);

        // Clicks/taps advance automatically; keyboard users pick with arrows and move on with “Next”.
        let viaPointer = false;
        form.addEventListener('pointerdown', () => { viaPointer = true; });
        form.addEventListener('keydown', () => { viaPointer = false; });

        form.addEventListener('change', (e) => {
            if (!e.target.matches('input[type="radio"]')) return;
            cancelAdvance();   // a second tap while the card is about to turn: turn once, not twice
            if (viaPointer && current < steps.length - 1) {
                advance = setTimeout(() => {
                    advance = null;
                    show(current + 1);
                    steps[current].querySelector('input')?.focus({preventScroll: true});
                }, 260);
            }
            show(current);  // progress bar + buttons
            if (viaPointer && current === steps.length - 1) submit.focus({preventScroll: true});
        });
        back.addEventListener('click', () => {
            cancelAdvance();
            show(current - 1);
            if (current === 0) steps[0].querySelector('input:checked, input')?.focus({preventScroll: true});  // “Back” just folded away
        });
        next.addEventListener('click', () => {
            show(current + 1);
            steps[current].querySelector('input:checked, input')?.focus({preventScroll: true});
        });

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const missing = steps.findIndex((_, n) => !answered(n));
            if (missing !== -1) {
                show(missing);
                return;
            }
            submit.disabled = true;
            try {
                // Same endpoint as the no-JS form: the server renders the result once, we just swap it in.
                const response = await fetch(form.action, {method: 'POST', body: new FormData(form)});
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const page = new DOMParser().parseFromString(await response.text(), 'text/html');
                const node = page.querySelector('[data-result]');
                if (!node) throw new Error('no result in response');
                form.replaceWith(node);
                bringIntoView(node);
                node.focus({preventScroll: true});
                // “Take it again” to this very page (a guide's #test): a same-page link wouldn't reload,
                // so bring the form back, empty, at the first card
                node.querySelector('[data-quiz-again]')?.addEventListener('click', (event) => {
                    if (event.currentTarget.pathname !== location.pathname) return;
                    event.preventDefault();
                    form.reset();
                    cancelAdvance();
                    settle();
                    current = 0;   // start over in place, not a swap from the last card
                    show(0);
                    submit.disabled = false;
                    node.replaceWith(form);
                    bringIntoView(form);
                    steps[0].querySelector('input')?.focus({preventScroll: true});
                });
            } catch {
                submit.disabled = false;
                form.submit();  // let the browser post the form normally
            }
        });
    });
})();
