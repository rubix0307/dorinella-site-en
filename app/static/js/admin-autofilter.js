/* A list filter without a button: choosing in a select (or leaving the search box) applies it at once; Enter in the search box works as always.
   A spinner stands where the button was. By the rule for every spinner it is invisible for the first 300 ms (CSS), so a quick answer shows none.
   Without the script the <noscript> button is there. Forms: `form[data-autofilter]` with a `[data-wait]` spinner inside. */
(() => {
    const forms = document.querySelectorAll('form[data-autofilter]');
    if (!forms.length) return;
    const apply = (form) => {
        const wait = form.querySelector('[data-wait]');
        if (wait) wait.hidden = false;
        form.requestSubmit();
    };
    forms.forEach((form) => {
        form.addEventListener('change', (event) => {
            if (event.target.matches('select, input[type="search"]')) apply(form);
        });
    });
    window.addEventListener('pageshow', () => forms.forEach((form) => {          // back to the page from the cache: no stale spinner
        const wait = form.querySelector('[data-wait]');
        if (wait) wait.hidden = true;
    }));
})();
