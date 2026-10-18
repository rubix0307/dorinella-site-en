// The /links/ page admin: «Save» without reloading the page. The form is sent as it is (photos too);
// the answer is the page itself (after the redirect, or with an error), and only its parts are taken
// over: the cards, the ads card, the counter. Scroll position, open panels' scripts and focus stay.
(() => {
    const form = document.querySelector('form[action$="/admin/links/save"]');
    if (!form) return;
    const status = form.querySelector('[data-save-status]');
    const submit = form.querySelector('button[type=submit]');
    let timer = 0;

    function say(text, kind) {
        clearTimeout(timer);
        status.textContent = text;
        status.className = `bio-save-status is-${kind} is-shown`;
        if (kind === 'ok') timer = setTimeout(() => status.classList.remove('is-shown'), 2600);
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        submit.disabled = true;
        submit.classList.add('is-loading');
        say(window.t('Saving…'), 'busy');
        try {
            const response = await fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin'});
            if (response.status === 403) throw new Error('forbidden');
            const page = new DOMParser().parseFromString(await response.text(), 'text/html');
            const fresh = page.querySelector('form[action$="/admin/links/save"]');
            if (!fresh) throw new Error('login');           // the session ended: the answer is the login page
            form.querySelector('.bio-rows').replaceChildren(...fresh.querySelector('.bio-rows').children);
            form.querySelector('[data-ads]').replaceWith(fresh.querySelector('[data-ads]'));
            form.querySelector('[data-header-card]').replaceWith(fresh.querySelector('[data-header-card]'));
            form.querySelector('[name=count]').value = fresh.querySelector('[name=count]').value;
            window.enhanceLinkSelects?.();
            const error = page.querySelector('.recipe-editor__error');
            if (error) say(error.textContent.trim(), 'error');
            else say(window.t('Saved. The page is updated at once.'), 'ok');
        } catch (err) {
            say(window.t(err.message === 'forbidden' ? 'You do not have the rights to change this page.' : err.message === 'login' ? 'Your session has ended. Reload the page and sign in again.' : 'Could not save: no connection. Please try again.'), 'error');
        } finally {
            submit.disabled = false;
            submit.classList.remove('is-loading');
        }
    });
})();
