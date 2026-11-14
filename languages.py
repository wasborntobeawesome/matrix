"""Content languages and per-language text preparation.

Uzbek keeps its own pipeline (uzbek.py: Cyrillic -> Latin, apostrophes, numbers).
Russian, English and Arabic text is passed through as written; Cyrillic must not
be transliterated for Russian. Numbers are spelled out with num2words for models
that can't read digits themselves.
"""
import re

from uzbek import expand_numbers as uz_expand_numbers
from uzbek import normalize as uz_normalize

# name: in the language itself (language picker); label: in Uzbek (hints, "· Rus aksenti").
LANGUAGES = {
    "uz": {"name": "Oʻzbekcha", "label": "Oʻzbek", "dir": "ltr"},
    "ru": {"name": "Русский", "label": "Rus", "dir": "ltr"},
    "en": {"name": "English", "label": "Ingliz", "dir": "ltr"},
    "ar": {"name": "العربية", "label": "Arab", "dir": "rtl"},
}

_PERCENT_WORD = {"ru": "процентов", "en": "percent", "ar": "بالمئة"}

# Digit-group separators: spaces everywhere, commas in English, the Arabic thousands mark.
_GROUP_SEP = {"ru": "[  ]", "en": "[,  ]", "ar": "[٬,  ]"}
_DECIMAL_MARK = {"ru": "[.,]", "en": "[.]", "ar": "[٫.,]"}
_EN_ORDINAL = re.compile(r"\b(\d+)(st|nd|rd|th)\b", re.IGNORECASE)


def _num2words():
    try:
        from num2words import num2words
    except ImportError:  # optional: without it digits are left for the model
        return None
    return num2words


def spell_numbers(text: str, lang: str) -> str:
    """'15,000 users, 3.5%' -> words in `lang` (ru, en or ar)."""
    n2w = _num2words()
    if n2w is None or lang not in _PERCENT_WORD:
        return text
    sep, dec = _GROUP_SEP[lang], _DECIMAL_MARK[lang]
    text = re.sub(rf"(?<!\d)(\d{{1,3}})((?:{sep}\d{{3}})+)(?![\d])", lambda m: m.group(1) + re.sub(r"\D", "", m.group(2)), text)
    if lang == "en":
        text = _EN_ORDINAL.sub(lambda m: n2w(int(m.group(1)), lang="en", to="ordinal"), text)

    def number(m):
        whole, frac = m.group(1), m.group(2)
        if frac:
            return n2w(float(f"{int(whole)}.{int(frac):0{len(frac)}d}"), lang=lang)
        return n2w(int(whole), lang=lang)

    num = rf"(\d+)(?:{dec}(\d+))?"
    text = re.sub(rf"{num}\s?[%٪]", lambda m: f"{number(m)} {_PERCENT_WORD[lang]}", text)
    return re.sub(rf"(?<![\d]){num}(?![\d])", number, text)


def prepare(text: str, lang: str, spell: bool) -> tuple[str, bool]:
    """Return (text ready for TTS, whether Uzbek Cyrillic was transliterated)."""
    if lang == "uz":
        text, converted = uz_normalize(text)
        return (uz_expand_numbers(text) if spell else text), converted
    text = re.sub(r"[ \t]+", " ", text).strip()
    return (spell_numbers(text, lang) if spell else text), False
