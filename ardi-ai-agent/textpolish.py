"""Text quality layer — runs on EVERY incoming message (microseconds, no API).

1. clean_text: normalize whitespace/punctuation, strip invisible junk that
   breaks matching, cap length.
2. detect_lang: am (Ethiopic script) / en (Latin) / mixed — fed to the AI so
   replies come back in the customer's language without aodel call.
"""
import re
import unicodedata

MAX_LEN = 2000

_ETHIOPIC_RE = re.compile(r"[\u1200-\u137F\u1380-\u139F\u2D80-\u2DDF\uAB00-\uAB2F]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_WS_RE = re.compile(r"\s+")
_REPEAT_PUNCT_RE = re.compile(r"([!?.,])\1{2,}")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def detect_lang(text: str) -> str:
    """am | en | mixed | unknown — script-based, no API call."""
    t = text or ""
    eth = len(_ETHIOPIC_RE.findall(t))
    lat = len(_LATIN_RE.findall(t))
    if eth and lat:
        return "mixed" if min(eth, lat) >= 3 else ("am" if eth > lat else "en")
    if eth:
        return "am"
    if lat:
        return "en"
    return "unknown"


def clean_text(text: str) -> str:
    """Normalize an incoming message. Never raises; never returns junk."""
    if not text:
        return ""
    t = unicodedata.normalize("NFC", str(text))
    # Invisible formatting / control chars (keep newlines).
    t = "".join(c for c in t if c == "\n" or not unicodedata.category(c).startswith("C"))
    # Curly quotes and exotic dashes -> ASCII.
    t = (t.replace("\u201c", '"').replace("\u201d", '"')
          .replace("\u2018", "'").replace("\u2019", "'")
          .replace("\u2013", "-").replace("\u2014", "-"))
    t = _WS_RE.sub(" ", t)
    t = _REPEAT_PUNCT_RE.sub(r"\1\1", t)
    t = _BLANK_LINES_RE.sub("\n\n", t)
    t = t.strip()
    if len(t) > MAX_LEN:
        t = t[:MAX_LEN].rstrip() + "…"
    return t


def prepare_incoming(text: str) -> dict:
    """One call for every inbound message -> {text, lang}."""
    cleaned = clean_text(text)
    return {"text": cleaned, "lang": detect_lang(cleaned)}


def tidy_reply(text: str) -> str:
    """Light outgoing polish: collapse blank runs, trim. Cheap and safe."""
    if not text:
        return ""
    return _BLANK_LINES_RE.sub("\n\n", text.strip())
