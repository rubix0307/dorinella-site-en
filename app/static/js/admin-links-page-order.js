// The /links/ page admin: reorder the button cards by dragging their right strip (or with arrow keys on
// it). The order on screen is the order on the page: each card's hidden b<i>_order is renumbered.
// The dragged card follows the pointer; the others slide out of its way (FLIP), and it settles into its
// slot on release.
(() => {
    const list = document.querySelector('.bio-rows');     // stays; only its cards are replaced on save
    if (!list) return;
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const rows = () => [...list.querySelectorAll('[data-bio-row]')];
    const HYSTERESIS = 100;     // px past the midpoint before a card that just swapped with us swaps back

    function renumber() {
        rows().forEach((row, i) => { row.querySelector('[data-order]').value = i + 1; });
    }

    // move `card` before `ref` (or to the end), sliding every other card from its old place
    function moveWithFlip(card, ref) {
        const others = rows().filter((r) => r !== card);
        const before = new Map(others.map((r) => [r, r.getBoundingClientRect().top]));
        list.insertBefore(card, ref);
        if (reduced) return;
        for (const r of others) {
            const dy = before.get(r) - r.getBoundingClientRect().top;
            if (dy) r.animate([{transform: `translateY(${dy}px)`}, {transform: 'none'}], {duration: 220, easing: 'cubic-bezier(.2, .8, .2, 1)'});
        }
    }

    let drag = null;     // {card, pointerId, startY, offset}

    list.addEventListener('pointerdown', (e) => {
        const grip = e.target.closest('[data-grip]');
        if (!grip || e.button !== 0) return;
        e.preventDefault();
        const card = grip.closest('[data-bio-row]');
        try { grip.setPointerCapture(e.pointerId); } catch { /* a synthetic pointer: moves still reach the list */ }
        drag = {card, grip, pointerId: e.pointerId, lastY: e.clientY, shift: 0};
        card.classList.add('is-dragging');
        document.body.classList.add('is-reordering');
    });

    list.addEventListener('pointermove', (e) => {
        if (!drag || e.pointerId !== drag.pointerId) return;
        drag.shift += e.clientY - drag.lastY;
        drag.lastY = e.clientY;
        const {card} = drag;
        // past the middle of a neighbour: take its place, and keep the card under the finger
        const all = rows(), i = all.indexOf(card);
        const prev = all[i - 1], next = all[i + 1];
        // layout positions (offsetTop), not the animated ones: a neighbour still sliding must not decide
        const mid = card.offsetTop + drag.shift + card.offsetHeight / 2;
        // swapping back with the card that just took our place needs 100 px more: no jitter at the midpoint
        const extra = (other) => (other === drag.lastSwap ? HYSTERESIS : 0);
        let swapped = null, other = null;
        if (next && mid > next.offsetTop + next.offsetHeight / 2 + extra(next)) {
            other = next;
            swapped = () => moveWithFlip(card, next.nextSibling);
        } else if (prev && mid < prev.offsetTop + prev.offsetHeight / 2 - extra(prev)) {
            other = prev;
            swapped = () => moveWithFlip(card, prev);
        }
        if (swapped) {
            const top = card.offsetTop;
            swapped();
            drag.shift -= card.offsetTop - top;
            drag.lastSwap = other;
        }
        card.style.transform = `translateY(${drag.shift}px)`;
    });

    function drop(e) {
        if (!drag || e.pointerId !== drag.pointerId) return;
        const {card, shift} = drag;
        drag = null;
        card.style.transform = '';
        document.body.classList.remove('is-reordering');
        const done = () => card.classList.remove('is-dragging');
        if (reduced || !shift) done();
        else card.animate([{transform: `translateY(${shift}px)`}, {transform: 'none'}], {duration: 200, easing: 'cubic-bezier(.2, .8, .2, 1)'}).finished.then(done, done);
        renumber();
    }
    list.addEventListener('pointerup', drop);
    list.addEventListener('pointercancel', drop);

    // keyboard: arrows on the handle move the card one place
    list.addEventListener('keydown', (e) => {
        const grip = e.target.closest('[data-grip]');
        if (!grip || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
        e.preventDefault();
        const card = grip.closest('[data-bio-row]'), all = rows(), i = all.indexOf(card);
        if (e.key === 'ArrowUp' && i > 0) moveWithFlip(card, all[i - 1]);
        else if (e.key === 'ArrowDown' && i < all.length - 1) moveWithFlip(card, all[i + 1].nextSibling);
        else return;
        renumber();
        grip.focus();
    });
})();

// delete: the trash on the right marks the card (it goes on save, «Restore» undoes); its lid opens on hover
(() => {
    const morph = (el, name) => window.morphIcon?.(el, name);
    document.addEventListener('pointerover', (e) => {
        const trash = e.target.closest?.('[data-trash]');
        if (trash && !trash.contains(e.relatedTarget)) morph(trash, 'trash-open');
    });
    document.addEventListener('pointerout', (e) => {
        const trash = e.target.closest?.('[data-trash]');
        if (trash && !trash.contains(e.relatedTarget) && trash.getAttribute('aria-pressed') !== 'true') morph(trash, 'trash');
    });
    function mark(row, on) {
        row.querySelector('[data-delete]').checked = on;
        row.classList.toggle('is-deleted', on);
        row.querySelector('.bio-row__deleted').hidden = !on;
        const trash = row.querySelector('[data-trash]');
        trash.setAttribute('aria-pressed', String(on));
        trash.setAttribute('aria-label', window.t(on ? 'Keep the button' : 'Delete the button'));
        morph(trash, on ? 'trash-open' : 'trash');
    }
    document.addEventListener('click', (e) => {
        const trash = e.target.closest('[data-trash]'), restore = e.target.closest('[data-restore]');
        const row = (trash || restore)?.closest('[data-bio-row]');
        if (row) mark(row, trash ? trash.getAttribute('aria-pressed') !== 'true' : false);
    });
})();

// the header photo: pick → shrunk in the browser to a 400 px square webp, shown at once, sent on save
(() => {
    async function shrink(file, size) {
        const bitmap = await createImageBitmap(file);
        const side = Math.min(bitmap.width, bitmap.height);
        const canvas = Object.assign(document.createElement('canvas'), {width: size, height: size});
        canvas.getContext('2d').drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, size, size);
        const blob = await new Promise((done) => canvas.toBlob(done, 'image/webp', 0.86));
        return new File([blob], 'photo.webp', {type: 'image/webp'});
    }
    document.addEventListener('click', (e) => {
        const box = e.target.closest('[data-header-photo]');
        if (!box) return;
        if (e.target.closest('[data-header-pick]')) box.querySelector('[data-header-file]').click();
        if (e.target.closest('[data-header-default]')) {
            box.querySelector('[data-header-file]').value = '';
            box.querySelector('[data-header-reset]').checked = true;
            const img = box.querySelector('img');
            img.src = img.dataset.default;
        }
    });
    document.addEventListener('change', async (e) => {
        const input = e.target.closest('[data-header-file]');
        if (!input?.files.length) return;
        const box = input.closest('[data-header-photo]');
        const pick = box.querySelector('[data-header-pick]');
        pick.classList.add('is-loading');
        try {
            const file = await shrink(input.files[0], 400);
            const transfer = new DataTransfer();
            transfer.items.add(file);
            input.files = transfer.files;
            box.querySelector('[data-header-reset]').checked = false;
            box.querySelector('img').src = URL.createObjectURL(file);
        } catch {
            input.value = '';
            window.adminConfirm({title: window.t('The photo could not be opened'), message: window.t('This photo could not be opened. Try a PNG or JPEG.'), confirmLabel: window.t('Got it'), cancelLabel: window.t('Close')});
        } finally {
            pick.classList.remove('is-loading');
        }
    });
})();
