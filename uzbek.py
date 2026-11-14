"""Uzbek text normalisation for TTS.

The Microsoft uz-UZ neural voices only understand Latin-script Uzbek
(Cyrillic input returns no audio), so Cyrillic text is transliterated to the
official Latin alphabet first.
"""
import re

# Unify the many apostrophe look-alikes people type for o‘ / g‘ and the tutuq belgisi.
_APOSTROPHES = re.compile(r"[`´‘’ʻʼʹ′']")

_CYR_LETTER = re.compile(r"[Ѐ-ӿ]")

_VOWELS = set("аеёиоуэюяўАЕЁИОУЭЮЯЎ")

# Single-letter map. "е" and "ц" are context dependent and handled in the loop.
_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ё": "yo", "ж": "j",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "x", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "'", "ы": "i", "ь": "",
    "э": "e", "ю": "yu", "я": "ya", "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h",
}


def has_cyrillic(text: str) -> bool:
    return bool(_CYR_LETTER.search(text))


def _match_case(src: str, out: str, nxt: str) -> str:
    """Apply the case of a Cyrillic source letter to its Latin output."""
    if not src.isupper() or not out:
        return out
    if len(out) == 1:
        return out.upper()
    # Multi-letter output (Sh, Ch, Yo, O'...): all caps only when the word is all caps.
    return out.upper() if nxt.isupper() else out[0].upper() + out[1:]


def cyrillic_to_latin(text: str) -> str:
    out = []
    n = len(text)
    for i, ch in enumerate(text):
        low = ch.lower()
        prev = text[i - 1] if i > 0 else ""
        nxt = text[i + 1] if i + 1 < n else ""
        word_start = not prev.isalpha()

        if low == "е":
            # "ye" at the start of a word or after a vowel / hard sign, else "e".
            lat = "ye" if word_start or prev in _VOWELS or prev.lower() in "ъь" else "e"
        elif low == "ц":
            # "s" at the start of a word or after a consonant, "ts" after a vowel.
            lat = "ts" if (not word_start and prev in _VOWELS) else "s"
        elif low in _MAP:
            lat = _MAP[low]
        else:
            out.append(ch)
            continue
        out.append(_match_case(ch, lat, nxt))
    return "".join(out)


def normalize(text: str) -> tuple[str, bool]:
    """Return (text ready for TTS, whether transliteration was applied)."""
    converted = has_cyrillic(text)
    if converted:
        text = cyrillic_to_latin(text)
    text = _APOSTROPHES.sub("'", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text, converted


# --------------------------------------------------------------------------
# Numbers -> words. Microsoft voices read digits themselves; local models
# generally don't, so we spell numbers out for them.
# --------------------------------------------------------------------------
_ONES = ["nol", "bir", "ikki", "uch", "to'rt", "besh", "olti", "yetti", "sakkiz", "to'qqiz"]
_TENS = ["", "o'n", "yigirma", "o'ttiz", "qirq", "ellik", "oltmish", "yetmish", "sakson", "to'qson"]
_SCALES = [(10**12, "trillion"), (10**9, "milliard"), (10**6, "million"), (1000, "ming")]


def _below_thousand(n: int) -> list[str]:
    words = []
    hundreds, rest = divmod(n, 100)
    if hundreds:
        if hundreds > 1:
            words.append(_ONES[hundreds])
        words.append("yuz")
    tens, ones = divmod(rest, 10)
    if tens:
        words.append(_TENS[tens])
    if ones:
        words.append(_ONES[ones])
    return words


def number_to_words(n: int) -> str:
    if n == 0:
        return "nol"
    if n < 0:
        return "minus " + number_to_words(-n)
    words = []
    for value, name in _SCALES:
        count, n = divmod(n, value)
        if count:
            if not (value == 1000 and count == 1):  # "ming", not "bir ming"
                words += _below_thousand(count)
            words.append(name)
    words += _below_thousand(n)
    return " ".join(words)


def ordinal_words(n: int) -> str:
    words = number_to_words(n)
    return words + ("nchi" if words[-1] in "aeiou" else "inchi")


_THOUSANDS_SEP = re.compile(r"(?<!\d)(\d{1,3})((?:[  ]\d{3})+)(?!\d)")
_DECIMAL = re.compile(r"(?<!\d)(\d+)[.,](\d+)(?!\d)")
_ORDINAL = re.compile(r"(?<!\d)(\d+)\s?-\s?(?=[^\W\d_])")
_PERCENT = re.compile(r"(\d+)\s?%")
_INTEGER = re.compile(r"\d+")


def expand_numbers(text: str) -> str:
    """'2026-yil 14-noyabr, 15 000 so'm, 3,5%' -> words."""
    text = _THOUSANDS_SEP.sub(lambda m: m.group(1) + re.sub(r"\D", "", m.group(2)), text)
    text = _PERCENT.sub(lambda m: m.group(1) + " foiz", text)
    text = _DECIMAL.sub(
        lambda m: f"{number_to_words(int(m.group(1)))} butun {number_to_words(int(m.group(2)))}", text
    )
    text = _ORDINAL.sub(lambda m: ordinal_words(int(m.group(1))) + " ", text)
    return _INTEGER.sub(lambda m: number_to_words(int(m.group())), text)


# --------------------------------------------------------------------------
# Latin -> Cyrillic, for models trained on Cyrillic text (Meta MMS).
# --------------------------------------------------------------------------
_LAT_DIGRAPHS = [
    ("o'", "ў"), ("g'", "ғ"), ("sh", "ш"), ("ch", "ч"),
    ("yo", "ё"), ("yu", "ю"), ("ya", "я"), ("ts", "ц"),
]
_LAT_SINGLE = {
    "a": "а", "b": "б", "d": "д", "f": "ф", "g": "г", "h": "ҳ", "i": "и",
    "j": "ж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
    "q": "қ", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "x": "х",
    "y": "й", "z": "з", "'": "ъ", "c": "к", "w": "в",
}


def latin_to_cyrillic(text: str) -> str:
    text = _APOSTROPHES.sub("'", text)
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        low2 = text[i:i + 2].lower()
        prev = text[i - 1] if i else ""
        word_start = not prev.isalpha() and prev != "'"
        if low2 == "ye" and word_start:
            cyr, step = "е", 2
        elif low2 in dict(_LAT_DIGRAPHS):
            cyr, step = dict(_LAT_DIGRAPHS)[low2], 2
        elif ch.lower() == "e":
            cyr, step = ("э" if word_start else "е"), 1
        elif ch.lower() in _LAT_SINGLE:
            cyr, step = _LAT_SINGLE[ch.lower()], 1
        else:
            out.append(ch)
            i += 1
            continue
        if ch.isupper():
            cyr = cyr.upper()
        out.append(cyr)
        i += step
    return "".join(out)
