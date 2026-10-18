/* Our own "are you sure?" for the admin, instead of the browser's confirm().

   window.adminConfirm({title, message, confirmLabel, cancelLabel, danger, typed}) -> Promise<boolean>
     danger: red button, role=alertdialog; typed: the word that must be typed before the button works (true = the word for "yes" of the admin's language; the English and the Russian word are accepted in both).
   Declarative: data-confirm="text" on a <form> or on a submit button (the button wins), with optional
     data-confirm-title, data-confirm-ok, data-confirm-danger (asks to type "yes"), data-confirm-typed (the same, without the red).
   The submit is caught in the capture phase before the page scripts see it; after "yes" the form is submitted again with the same
   button (so formaction, name and value stay) and that second submit passes through. Without JS the form simply submits. */
(() => {
    const ACCEPTED = ['yes', '\u0434\u0430'];         // "yes" in English and in Russian: either is accepted in both languages
    const word = () => t('yes');                         // the word the admin is asked to type
    const T = {title: () => t('Confirm the action'), ok: () => t('Yes'), cancel: () => t('Cancel'), dangerOk: () => t('Delete'),
               label: () => t('To confirm, type "yes"')};
    let open = null;                                    // the question on screen: one at a time
    let uid = 0;

    function adminConfirm(options = {}) {
        if (open) return open.promise.then(() => adminConfirm(options));     // the next question waits for the first to close (a Back must not be cancelled silently); the answer is never shared
        const o = {...options};
        const danger = !!o.danger;
        const asked = o.typed === true || (o.typed === undefined && danger) ? word() : o.typed || '';
        const id = `confirm-${++uid}`;
        const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const dialog = document.createElement('dialog');
        dialog.className = 'sheet sheet--confirm adc';
        if (danger) dialog.setAttribute('role', 'alertdialog');
        dialog.setAttribute('aria-labelledby', `${id}-t`);
        dialog.setAttribute('aria-describedby', `${id}-m`);
        const form = document.createElement('form');
        form.method = 'dialog';
        form.className = 'adc__form';
        form.noValidate = true;
        const head = document.createElement('h2');
        head.className = 'adc__title';
        head.id = `${id}-t`;
        head.textContent = o.title || T.title();
        const text = document.createElement('p');
        text.className = 'adc__text';
        text.id = `${id}-m`;
        text.textContent = o.message || '';
        form.append(head, text);
        let input = null, hint = null;
        if (asked) {
            const label = document.createElement('label');
            label.className = 'adc__field';
            const span = document.createElement('span');
            span.textContent = T.label();
            input = document.createElement('input');
            input.type = 'text';
            input.className = 'adc__input';
            input.autocomplete = 'off';
            input.autocapitalize = 'off';
            input.spellcheck = false;
            input.setAttribute('enterkeyhint', 'done');
            label.append(span, input);
            hint = document.createElement('p');         // tells a screen reader when the button becomes available
            hint.className = 'sr-only';
            hint.setAttribute('role', 'status');
            form.append(label, hint);
        }
        const foot = document.createElement('div');
        foot.className = 'adc__foot';
        const cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.className = 'btn btn--ghost';
        cancel.textContent = o.cancelLabel || T.cancel();
        const ok = document.createElement('button');
        ok.type = 'submit';
        ok.className = `btn ${danger ? 'btn--danger' : 'btn--primary'}`;
        ok.textContent = o.confirmLabel || (danger ? T.dangerOk() : T.ok());
        foot.append(cancel, ok);
        form.append(foot);
        dialog.append(form);

        const typedOk = () => !input || ACCEPTED.includes(input.value.trim().toLowerCase());
        const sync = () => { ok.disabled = !typedOk(); if (hint) hint.textContent = ok.disabled ? '' : t('Now you can press "%(button)s"', {button: ok.textContent}); };
        input?.addEventListener('input', sync);
        sync();

        let answer = false, closing = false;
        const promise = new Promise((resolve) => {
            const finish = () => {
                closing = true;
                dialog.classList.add('is-closing');
                const done = () => { dialog.remove(); open = null; resolve(answer); if (opener?.isConnected) opener.focus({preventScroll: true}); };     // `open` stays until the dialog is gone: no second question starts in the closing motion
                if (matchMedia('(prefers-reduced-motion: reduce)').matches) done(); else setTimeout(done, 160);
            };
            form.addEventListener('submit', (event) => {
                event.preventDefault();
                if (closing) return;                        // already answered: a second click or Enter while the dialog leaves
                if (!typedOk()) { input?.focus(); return; }
                answer = true;
                dialog.close();
            });
            cancel.addEventListener('click', () => { if (!closing) dialog.close(); });
            let downOnBackdrop = false;                 // a selection dragged out of the input ends on the backdrop: that is not a click on it
            dialog.addEventListener('mousedown', (event) => { downOnBackdrop = event.target === dialog; });
            dialog.addEventListener('click', (event) => { if (event.target === dialog && downOnBackdrop) dialog.close(); downOnBackdrop = false; });    // the backdrop is part of the dialog
            dialog.addEventListener('close', finish, {once: true});            // Esc, the buttons and the backdrop all end here
        });
        open = {promise};
        document.body.append(dialog);
        dialog.showModal();
        const touch = matchMedia('(pointer: coarse)').matches;          // a phone: the keyboard would cover the buttons of the sheet, so it opens only when the admin taps the field
        (input && !touch ? input : danger ? cancel : ok).focus({preventScroll: true});          // a dangerous question starts on the safe button
        return promise;
    }
    window.adminConfirm = adminConfirm;
    document.documentElement.classList.add('js-ready');     // the dangerous forms are unlocked (admin.css keeps them locked until the script is here)

    const settings = (el) => {
        const d = el.dataset;
        return {
            message: d.confirm, title: d.confirmTitle, confirmLabel: d.confirmOk,
            danger: 'confirmDanger' in d, typed: 'confirmDanger' in d || 'confirmTyped' in d ? true : undefined,
        };
    };
    let passing = null;                                 // the form being submitted after a "yes": its submit event is not asked about again
    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (passing === form) { passing = null; return; }
        const by = event.submitter;
        const source = by?.hasAttribute('data-confirm') ? by : form.hasAttribute('data-confirm') ? form : null;
        if (!source) return;
        event.preventDefault();
        event.stopImmediatePropagation();               // the soft navigation and the page scripts hear only the confirmed submit
        if (open) return;                               // a second click while the question is on screen
        adminConfirm(settings(source)).then((yes) => {
            if (!yes || !form.isConnected) return;
            passing = form;
            if (by && by.form === form && by.isConnected) form.requestSubmit(by); else form.requestSubmit();
            setTimeout(() => { if (passing === form) passing = null; });         // requestSubmit fires the event at once; if it was refused by validation, nothing stays armed
        });
    }, true);
})();
