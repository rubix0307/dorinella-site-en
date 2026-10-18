// Sub-links under a button: add / remove rows. Rows are renumbered after every change (b{i}_s{j}_*) so the
// server, which reads sub_count indices in order, never sees a gap. Delegated, so it survives the AJAX save
// that swaps the button cards.
(() => {
    const form = document.querySelector('.recipe-editor__form');
    if (!form) return;

    function sync(box) {
        const rows = [...box.querySelectorAll('[data-sub]')];
        const i = box.closest('[data-bio-row]').querySelector('[data-order]').name.match(/^b(\d+)_/)[1];
        rows.forEach((row, j) => {
            row.querySelectorAll('[name]').forEach((el) => { el.name = el.name.replace(/^b\d+_s\d+_/, `b${i}_s${j}_`); });
        });
        box.querySelector('[data-sub-count]').value = rows.length;
        box.querySelector('[data-sub-add]').hidden = rows.length >= Number(box.dataset.max);
    }

    form.addEventListener('click', (e) => {
        const add = e.target.closest('[data-sub-add]');
        const remove = e.target.closest('[data-sub-remove]');
        const box = (add || remove)?.closest('[data-subs]');
        if (!box) return;
        if (add) {
            const list = box.querySelector('[data-sub-list]');
            list.append(box.querySelector('[data-sub-template]').content.cloneNode(true));
            sync(box);
            window.enhanceLinkSelects?.();
            list.lastElementChild.querySelector('input')?.focus();
        } else {
            remove.closest('[data-sub]').remove();
            sync(box);
        }
    });
})();
