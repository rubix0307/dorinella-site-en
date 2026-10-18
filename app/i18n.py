"""The language of the admin: English (the source language of every string) and Russian.

The public pages are English only and never go through here. In the admin every visible string is written in English in the code and the templates, and
looked up in `app/locale/ru.py` (English -> Russian) when the admin speaks Russian. A string that has no translation is shown in English: a missing entry is
never an error (tests/test_i18n.py lists the gaps).

    {{ _('Users') }}                          a fixed string in a template
    {{ label|t }}                             a string that comes from the code (a permission label, a status)
    {{ _('Delete %(email)s?', email=x) }}     with values (the named %(…)s placeholders are kept in the translation)

A message made in Python that carries values is a `Message`: it reads as the finished English sentence, and `|t` translates its template and fills the values.
Scripts: base.html hands `js_strings(lang)` (the "js" section of ru.py) to static/js/admin-i18n.js, which defines `window.t('English', {name: value})`.

Which language: the cookie `admin_lang` (set by the switcher in the admin header, POST /admin/language), else the browser's Accept-Language (Russian, Ukrainian and
Belarusian browsers get Russian), else English.
"""
from fastapi import Request

from .locale.ru import JS, RU

LANGS = {'en': 'English', 'ru': 'Русский'}
DEFAULT = 'en'
COOKIE = 'admin_lang'


class Message(str):
    """An English sentence made of a template and its values: `str(message)` is the English text, `translate` finds the template in the dictionary."""

    template: str
    values: dict

    def __new__(cls, template: str, **values):
        self = super().__new__(cls, template % values if values else template)
        self.template, self.values = template, values
        return self


def pick(request: Request) -> str:
    """The language of this request."""
    chosen = request.cookies.get(COOKIE, '')
    if chosen in LANGS:
        return chosen
    for part in request.headers.get('accept-language', '').split(','):
        code = part.split(';')[0].strip().lower()[:2]
        if code in ('ru', 'uk', 'be'):
            return 'ru'
        if code == 'en':
            return 'en'
    return DEFAULT


def translate(text: str, lang: str, **values) -> str:
    """`text` in `lang` (English when there is no translation), with the %(name)s placeholders filled."""
    if isinstance(text, Message):
        values = {**text.values, **values}
        text = text.template
    values = {name: translate(value, lang) if isinstance(value, Message) else value for name, value in values.items()}   # a part that is a message itself
    out = RU.get(text, text) if lang == 'ru' else text
    return out % values if values else out


def js_strings(lang: str) -> dict[str, str]:
    """What the admin scripts translate: nothing for English (the script's own strings are English)."""
    return JS if lang == 'ru' else {}


def install(env) -> None:
    """Make `_()` and the `t` filter of the language of the current request available in the templates (called by core/templating.setup)."""
    import jinja2

    @jinja2.pass_context
    def gettext(context, text: str, **values) -> str:
        return translate(text, context.get('lang', DEFAULT), **values)

    @jinja2.pass_context
    def t_filter(context, text, **values):
        return translate(text, context.get('lang', DEFAULT), **values) if isinstance(text, str) else text

    env.globals['_'] = gettext
    env.filters['t'] = t_filter
    env.globals['js_strings'] = js_strings
