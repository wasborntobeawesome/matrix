import pytest
from uzbek import cyrillic_to_latin, normalize


@pytest.mark.parametrize("cyr, lat", [
    ("Ўзбекистон", "O'zbekiston"),
    ("Тошкент", "Toshkent"),
    ("шаҳар", "shahar"),
    ("қизил", "qizil"),
    ("ғалаба", "g'alaba"),
    ("Ер", "Yer"),            # е at word start -> ye
    ("келди", "keldi"),       # е after consonant -> e
    ("оилае", "oilaye"),      # е after vowel -> ye
    ("цирк", "sirk"),         # ц at word start -> s
    ("милиция", "militsiya"), # ц after vowel -> ts
    ("маъно", "ma'no"),
    ("Ёшлар", "Yoshlar"),
    ("ЧОРШАНБА", "CHORSHANBA"),
    ("Юлдуз", "Yulduz"),
    ("Шаҳар 2026", "Shahar 2026"),
])
def test_cyrillic_to_latin(cyr, lat):
    assert cyrillic_to_latin(cyr) == lat


def test_normalize_unifies_apostrophes():
    text, converted = normalize("Oʻzbek  go’zal sanʼat")
    assert text == "O'zbek go'zal san'at"
    assert converted is False


def test_normalize_flags_cyrillic():
    text, converted = normalize("Салом, дунё!")
    assert text == "Salom, dunyo!"
    assert converted is True


from uzbek import expand_numbers, latin_to_cyrillic, number_to_words  # noqa: E402


@pytest.mark.parametrize("n, words", [
    (0, "nol"), (7, "yetti"), (14, "o'n to'rt"), (100, "yuz"), (215, "ikki yuz o'n besh"),
    (1000, "ming"), (2026, "ikki ming yigirma olti"), (15000, "o'n besh ming"),
    (3_400_000, "uch million to'rt yuz ming"),
])
def test_number_to_words(n, words):
    assert number_to_words(n) == words


@pytest.mark.parametrize("src, out", [
    ("2026-yil 14-noyabr", "ikki ming yigirma oltinchi yil o'n to'rtinchi noyabr"),
    ("narxi 15 000 so'm", "narxi o'n besh ming so'm"),
    ("3,5% o'sdi", "uch butun besh foiz o'sdi"),
    ("30 dan ortiq", "o'ttiz dan ortiq"),
    ("1-sinf", "birinchi sinf"),
])
def test_expand_numbers(src, out):
    assert expand_numbers(src) == out


@pytest.mark.parametrize("lat, cyr", [
    ("O'zbekiston", "Ўзбекистон"),
    ("Toshkent shahri", "Тошкент шаҳри"),
    ("g'alaba", "ғалаба"),
    ("yer", "ер"),
    ("eshik", "эшик"),
    ("keldi", "келди"),
    ("ma'no", "маъно"),
    ("Yoshlar", "Ёшлар"),
])
def test_latin_to_cyrillic(lat, cyr):
    assert latin_to_cyrillic(lat) == cyr
