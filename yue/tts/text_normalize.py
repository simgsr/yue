"""Language-aware text normalization for the CosyVoice2 backend.

CosyVoice2 is strong at Simplified Chinese but its English text normalization
is weak: raw digits ("34"), dates, times, decimals, percentages and
abbreviations are often read verbatim (e.g. "34" -> "three four") instead of
naturally, and Traditional Chinese characters are read poorly. This module
spells such tokens out (dates, times, numbers, ...) so the model reads them
correctly, and converts Traditional Chinese to Simplified so it is read well.

Chinese numerals are otherwise left as-is: CosyVoice2 already normalizes
Simplified Chinese numerals well.
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

# Time: 5:30, 12:05 (bare), and 5:30am / 5:30 pm / 5:30 p.m. (with meridiem).
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
# Attached/no-dot meridiem: 5:30am, 5:30 pm. Dotted forms ("5:30 p.m.") are
# handled by the abbreviation expansion instead (their trailing "." breaks \b).
_TIME_MERIDIEM_RE = re.compile(
    r"\b((?:[01]?\d|2[0-3]):[0-5]\d)\s*(am|pm)\b", re.IGNORECASE
)

# Dates. Supported: ISO (2024-01-15), year-first slash (2024/01/15), US slash
# (12/25/2024), and month-name / day-month-name forms ("January 15, 2024",
# "Jan. 5 2024", "15 January 2024").
_ISO_DATE_RE = re.compile(r"\b(20\d{2}|19\d{2})-(\d{1,2})-(\d{1,2})\b")
_YEAR_SLASH_DATE_RE = re.compile(r"\b(20\d{2}|19\d{2})/(\d{1,2})/(\d{1,2})\b")
_US_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

_MONTH_WORD = (
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|"
    r"Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)"
)
_MONTH_DATE_FIRST_RE = re.compile(
    rf"\b({_MONTH_WORD})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{{4}}))?\b",
    re.IGNORECASE,
)
_DAY_DATE_FIRST_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_WORD})(?:\s*,?\s*(\d{{4}}))?\b",
    re.IGNORECASE,
)

_MONTHS = [
    None, "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_MONTH_NUM = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

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

# Lazy OpenCC converter (Traditional -> Simplified Chinese).
_t2s = None


def _has_cjk(text: str) -> bool:
    """True if the text contains CJK characters or CJK punctuation."""
    return any(
        (0x3400 <= ord(c) <= 0x4DBF) or   # CJK ext A
        (0x4E00 <= ord(c) <= 0x9FFF) or   # CJK unified
        (0x3000 <= ord(c) <= 0x303F) or   # CJK punctuation
        (0xFF00 <= ord(c) <= 0xFFEF)      # fullwidth forms
        for c in text
    )


def _is_latin(text: str) -> bool:
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        # No script letters (pure digits/punctuation): treat as Latin unless the
        # punctuation is CJK, so standalone times/dates still normalize while a
        # digit-only Chinese line ("123，456。") stays untouched.
        return not _has_cjk(text)
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


def _replace_time_meridiem(match: re.Match) -> str:
    time_part = _TIME_RE.match(match.group(1))
    base = _replace_time(time_part)
    return base + (" a m" if match.group(2).lower().startswith("a") else " p m")


def _month_number(name: str) -> int | None:
    return _MONTH_NUM.get(name.rstrip(".").lower())


def _spoken_ymd(year: str, month: str, day: str) -> str | None:
    """'2024', '1', '15' -> 'January fifteenth, twenty twenty-four'."""
    try:
        month_i, day_i, year_i = int(month), int(day), int(year)
    except ValueError:
        return None
    if not (1 <= month_i <= 12 and 1 <= day_i <= 31):
        return None
    return f"{_MONTHS[month_i]} {num2words(day_i, ordinal=True)}, {_year_style(year_i)}"


def _replace_iso_date(match: re.Match) -> str:
    return _spoken_ymd(match.group(1), match.group(2), match.group(3)) or match.group(0)


def _replace_year_slash_date(match: re.Match) -> str:
    return _spoken_ymd(match.group(1), match.group(2), match.group(3)) or match.group(0)


def _replace_us_date(match: re.Match) -> str:
    return _spoken_ymd(match.group(3), match.group(1), match.group(2)) or match.group(0)


def _replace_month_date_first(match: re.Match) -> str:
    mon = _month_number(match.group(1))
    if mon is None:
        return match.group(0)
    if match.group(3):
        return _spoken_ymd(match.group(3), str(mon), match.group(2)) or match.group(0)
    try:
        return f"{_MONTHS[mon]} {num2words(int(match.group(2)), ordinal=True)}"
    except ValueError:
        return match.group(0)


def _replace_day_date_first(match: re.Match) -> str:
    mon = _month_number(match.group(2))
    if mon is None:
        return match.group(0)
    day = match.group(1)
    # "fifteenth of January, ..." (no leading "the": the surrounding text often
    # already supplies it, e.g. "the 15 January").
    if match.group(3):
        try:
            return (
                f"{num2words(int(day), ordinal=True)} of {_MONTHS[mon]}, "
                f"{_year_style(int(match.group(3)))}"
            )
        except ValueError:
            return match.group(0)
    try:
        return f"{num2words(int(day), ordinal=True)} of {_MONTHS[mon]}"
    except ValueError:
        return match.group(0)


def _replace_integer(match: re.Match) -> str:
    token = match.group(0)
    bare_4digit = "," not in token and len(token) == 4
    return _cardinal(int(token.replace(",", "")), bare_4digit)


def _expand_abbreviations(text: str) -> str:
    return " ".join(_ABBREVIATIONS.get(w.lower(), w) for w in text.split(" "))


def _to_simplified(text: str) -> str:
    """Convert Traditional Chinese to Simplified via OpenCC (lazy)."""
    global _t2s
    if _t2s is None:
        from opencc import OpenCC
        _t2s = OpenCC("t2s")
    return _t2s.convert(text)


def normalize_tts_text(text: str, lang: str = "") -> str:
    """Return ``text`` with numbers/dates/times/abbreviations spelled out.

    English text is rewritten to natural spoken forms. Chinese text (Traditional
    or Simplified) is left as numerals but Traditional characters are converted
    to Simplified so CosyVoice2 reads them correctly.
    """
    if not text or not text.strip():
        return text
    if not _is_latin(text):
        # Non-Latin (predominantly Chinese) text: CosyVoice2 reads Simplified
        # well but not Traditional, so convert Traditional -> Simplified.
        if _has_cjk(text):
            try:
                return _to_simplified(text)
            except Exception:  # noqa: BLE001
                return text
        return text

    # Order matters: percentages/money/time/dates before plain integers so the
    # integer sub-part isn't separately rewritten into a cardinal.
    t = _PERCENT_RE.sub(_replace_percent, text)
    t = _MONEY_RE.sub(_replace_money, t)
    t = _TIME_MERIDIEM_RE.sub(_replace_time_meridiem, t)
    t = _TIME_RE.sub(_replace_time, t)
    t = _ISO_DATE_RE.sub(_replace_iso_date, t)
    t = _YEAR_SLASH_DATE_RE.sub(_replace_year_slash_date, t)
    t = _US_DATE_RE.sub(_replace_us_date, t)
    t = _MONTH_DATE_FIRST_RE.sub(_replace_month_date_first, t)
    t = _DAY_DATE_FIRST_RE.sub(_replace_day_date_first, t)
    t = _ORDINAL_RE.sub(_replace_ordinal, t)
    t = _DECIMAL_RE.sub(_replace_decimal, t)
    t = _INTEGER_RE.sub(_replace_integer, t)
    t = _expand_abbreviations(t)
    return t
