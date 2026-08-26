"""Language-aware text normalization for the CosyVoice2 backend.

CosyVoice2 is strong at Chinese but its English text normalization is weak:
raw digits ("34"), dates, decimals, percentages and abbreviations are often
read verbatim (e.g. "34" -> "three four") instead of naturally. This module
spells such tokens out so the model reads them correctly.

Chinese is left as-is: CosyVoice2 already normalizes Chinese numerals well.
"""

import re

from num2words import num2words

# English ordinal suffix pattern: 1st, 2nd, 3rd, 4th, 10th, 21st, 34th, 101st...
_ORDINAL_RE = re.compile(r"\b\d{1,6}(?:st|nd|rd|th)\b", re.IGNORECASE)

# Integer: bare (34, 2024) or with thousands separators (3,400, 34000000).
_INTEGER_RE = re.compile(r"\b\d{1,9}(?:,\d{3})+\b|\b\d{1,9}\b")

# Decimal: 3.14, 2.5
_DECIMAL_RE = re.compile(r"\b\d{1,3}\.\d{1,6}\b")

# Percentage: 50%, 12.5%  (note: no trailing \b — % is a non-word char)
_PERCENT_RE = re.compile(r"\b\d{1,9}(?:\.\d{1,6})?%")

# Money: $5, $5.50, £3, €2
_MONEY_RE = re.compile(r"[£$€]\s*(\d{1,9}(?:\.\d{1,2})?)\b")

# Time: 5:30, 12:05
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")

# Common English abbreviations to expand (case-insensitive), mapped to spoken form.
# "St." is deliberately excluded: it is ambiguous (Saint vs. Street).
_ABBREVIATIONS = {
    "mr.": "mister", "mrs.": "missis", "ms.": "miz", "dr.": "doctor",
    "prof.": "professor", "rev.": "reverend",
    "ave.": "avenue", "blvd.": "boulevard", "rd.": "road", "ln.": "lane",
    "co.": "company", "inc.": "incorporated", "ltd.": "limited",
    "corp.": "corporation", "vs.": "versus", "etc.": "et cetera",
    "e.g.": "for example", "i.e.": "that is", "a.m.": "a m", "p.m.": "p m",
    "no.": "number", "gov.": "governor", "gen.": "general",
    "capt.": "captain", "col.": "colonel", "sgt.": "sergeant",
    "jr.": "junior", "sr.": "senior", "ph.d.": "ph d", "m.d.": "m d",
}

# Only apply English normalization when the text is predominantly Latin script.
_LATIN_THRESHOLD = 0.7


def _is_latin(text: str) -> bool:
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    latin = sum(1 for c in letters if ord(c) < 0x4E00)
    return latin / len(letters) >= _LATIN_THRESHOLD


def _year_style(n: int) -> str:
    """Render a plausible year 1000-2099 naturally (2024 -> 'twenty twenty-four')."""
    if n == 2000:
        return "two thousand"
    hi, lo = divmod(n, 100)
    if lo == 0:
        return f"{num2words(hi)} hundred"  # 1900 -> nineteen hundred
    if hi == 20 and n < 2010:
        return f"two thousand {num2words(lo)}"  # 2001-2009
    return f"{num2words(hi)} {num2words(lo)}"  # 2024 -> twenty twenty-four


def _cardinal(n: int, bare_4digit: bool) -> str:
    """Integer to words. A bare 4-digit value in 1000-2099 reads as a year;
    a comma-grouped number (e.g. 1,234) is always a quantity."""
    if bare_4digit and 1000 <= n <= 2099:
        return _year_style(n)
    return num2words(n)


def _num_to_words(token: str) -> str:
    """Cardinal words for a digit token, decimal or integer."""
    if "." in token:
        whole, frac = token.split(".", 1)
        whole_words = num2words(int(whole)) if whole else "zero"
        frac_words = " ".join(num2words(int(d)) for d in frac)
        return f"{whole_words} point {frac_words}"
    return num2words(int(token))


def _replace_ordinal(match: re.Match) -> str:
    token = match.group(0)
    try:
        return num2words(int(token[:-2]), ordinal=True)
    except Exception:  # noqa: BLE001
        return token


def _replace_decimal(match: re.Match) -> str:
    return _num_to_words(match.group(0))


def _replace_percent(match: re.Match) -> str:
    return f"{_num_to_words(match.group(0)[:-1])} percent"


def _replace_money(match: re.Match) -> str:
    amount = match.group(1)
    if "." in amount:
        dollars, cents = amount.split(".", 1)
        dwords = num2words(int(dollars))
        cents_num = int(cents.ljust(2, "0")[:2])
        if cents_num:
            return f"{dwords} dollars and {num2words(cents_num)} cents"
        return f"{dwords} dollars"
    return f"{num2words(int(amount))} dollars"


def _replace_time(match: re.Match) -> str:
    h, m = int(match.group(1)), int(match.group(2))
    hour = num2words(h)
    if m == 0:
        return f"{hour} o'clock"
    return f"{hour} {num2words(m)}"


def _replace_integer(match: re.Match) -> str:
    token = match.group(0)
    bare_4digit = "," not in token and len(token) == 4
    return _cardinal(int(token.replace(",", "")), bare_4digit)


def _expand_abbreviations(text: str) -> str:
    return " ".join(_ABBREVIATIONS.get(w.lower(), w) for w in text.split(" "))


def normalize_tts_text(text: str, lang: str = "") -> str:
    """Return ``text`` with numbers/dates/abbreviations spelled out.

    Only English text is rewritten; Chinese (and the default zh lang) pass
    through unchanged.
    """
    if not text or not text.strip():
        return text
    # Normalize whenever the text is predominantly Latin script (English),
    # regardless of the selected voice's language. Chinese text passes through
    # unchanged — CosyVoice2 already reads Chinese numerals well.
    if not _is_latin(text):
        return text

    # Order matters: percentages/money/time before plain integers so the integer
    # sub-part isn't separately rewritten into a cardinal.
    t = _PERCENT_RE.sub(_replace_percent, text)
    t = _MONEY_RE.sub(_replace_money, t)
    t = _TIME_RE.sub(_replace_time, t)
    t = _ORDINAL_RE.sub(_replace_ordinal, t)
    t = _DECIMAL_RE.sub(_replace_decimal, t)
    t = _INTEGER_RE.sub(_replace_integer, t)
    t = _expand_abbreviations(t)
    return t
