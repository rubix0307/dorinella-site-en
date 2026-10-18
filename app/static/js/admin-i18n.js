// Strings of the admin scripts: base.html puts the Russian ones (ru.py, section JS) in #admin-i18n when the admin speaks Russian; English is the source text.
(() => {
    let dict = {};
    try { dict = JSON.parse(document.getElementById('admin-i18n')?.textContent || '{}'); } catch { /* English */ }
    window.adminLang = document.documentElement.lang || 'en';
    window.t = (text, values) => {
        const out = dict[text] ?? text;
        return values ? out.replace(/%\((\w+)\)s/g, (_, name) => values[name] ?? '') : out;
    };
})();

// admin-confirm.js did not come: the dangerous forms must not stay locked (see `js-ready` in admin.css)
window.addEventListener('load', () => {
    if (!document.documentElement.classList.contains('js-ready')) document.documentElement.classList.replace('js', 'no-js');
});
