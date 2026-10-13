"""Heuristic parse helpers for third-party bot-detection test pages.

These helpers take *static page HTML text* (captured after the page's
JavaScript has run) and reduce it to a small, uniform verdict dict::

    {'verdict': 'clean' | 'suspicious' | 'bot' | 'unknown',
     'signals': [matched snippets, max 3, truncated]}

Design intent
-------------
* Both CreepJS (https://abrahamjuliot.github.io/creepjs/) and Pixelscan
  (https://pixelscan.net) are heavily JS-driven and their markup changes
  often. Everything here is therefore a *heuristic*, not an exact
  detection: we regex-scan the rendered text for verdict-like words and
  phrases instead of relying on element IDs or CSS classes that rot.
* A 'bot'/'suspicious' verdict from one of these sites is a *signal*, not
  proof of detection; consumers should treat it accordingly.
* On anything ambiguous, unmatched, or internally broken, the helpers
  return ``{'verdict': 'unknown', 'signals': [...]}`` rather than
  guessing. They never raise.

Verdict precedence (per helper): explicit bot-detection language beats
suspicious indicators, which beat clean indicators, which beat nothing
('unknown'). When a trust/score percentage is found but no explicit
wording, the number nudges the verdict as documented on each helper.
"""

import src._vendor  # noqa: F401  (must stay first: keeps vendored deps importable)

import re

# Max matched snippets kept per result; longer than this we drop.
_MAX_SIGNALS = 3
# Snippets are whitespace-collapsed and truncated to this length.
_SIGNAL_SNIPPET_LEN = 120


def _snippet(text):
    """Collapse whitespace and truncate one matched snippet."""
    try:
        collapsed = " ".join(str(text).split())
    except Exception:
        return ""
    if len(collapsed) > _SIGNAL_SNIPPET_LEN:
        return collapsed[:_SIGNAL_SNIPPET_LEN].rstrip() + "..."
    return collapsed


def _collect(pattern, text, limit=_MAX_SIGNALS):
    """Return up to `limit` snippet matches for `pattern` in `text`."""
    signals = []
    try:
        for match in re.finditer(pattern, text or "", re.IGNORECASE):
            snippet = _snippet(match.group(0))
            if snippet and snippet not in signals:
                signals.append(snippet)
            if len(signals) >= limit:
                break
    except Exception:
        pass
    return signals


# --- shared regex vocabulary -------------------------------------------------

# Explicit bot/automation wording (strong signal).
_BOT_WORDING = r"(?:detected\s+as\s+a?\s*bot|bot\s+detected|you\s+are\s+a?\s*bot|headless\s+(?:browser|mode|detected)|webdriver\s+(?:detected|present|flag)|automation\s+(?:detected|flagged)|(?:puppeteer|selenium|playwright)\s+detected)"

# Suspicious/fake-fingerprint wording (medium signal).
_SUSPICIOUS_WORDING = r"(?:\blies?\b|spoof(?:ed|ing)?|fake\s+fingerprint|mismatch|inconsisten\w*|suspicious|tamper\w*|unusual|anomal\w*)"

# Clean/pass wording (weak signal, only used when no bot/suspicious hit).
_CLEAN_WORDING = r"(?:you\s+are\s+real|no\s+lies|passed|no\s+issues\s+detected|looks?\s+real|human[-\s]?like)"

# A percentage near the word "trust" (CreepJS renders a trust score).
_TRUST_PCT = r"trust(?:\s*score)?[^0-9]{0,40}([0-9]{1,3})\s*%"


def _trust_percentage(text):
    """Return the first trust-adjacent percentage as int, else None."""
    try:
        match = re.search(_TRUST_PCT, text or "", re.IGNORECASE)
        if match:
            value = int(match.group(1))
            if 0 <= value <= 100:
                return value
    except Exception:
        pass
    return None


def _verdict_dict(verdict, signals):
    """Build the canonical result dict, defensive to the end."""
    if verdict not in ("clean", "suspicious", "bot", "unknown"):
        verdict = "unknown"
    try:
        signals = [str(s) for s in signals][: _MAX_SIGNALS]
    except Exception:
        signals = []
    return {"verdict": verdict, "signals": signals}


# --- public helpers ----------------------------------------------------------


def parse_creepjs(html):
    """Heuristically parse a rendered CreepJS result page.

    CreepJS (https://abrahamjuliot.github.io/creepjs/) renders fingerprint
    and "trust" information into the page after heavy client-side JS runs.
    Because its markup changes often, this scans the rendered text for
    bot/trust signals instead of depending on any DOM structure:

    * explicit bot-detection wording -> 'bot'
    * "lies"/spoof/mismatch-type wording -> 'suspicious'
    * a trust percentage near 'trust': >= 90 -> 'clean', < 90 ->
      'suspicious' (a high trust score is CreepJS's way of saying the
      fingerprint reads as a real browser)
    * explicit clean wording ('no lies', 'you are real', ...) -> 'clean'
    * nothing matched -> 'unknown'

    Never raises; unmatched or broken input yields 'unknown'.
    """
    try:
        text = html if isinstance(html, str) else ""
    except Exception:
        return _verdict_dict("unknown", [])
    try:
        bot_signals = _collect(_BOT_WORDING, text)
        if bot_signals:
            return _verdict_dict("bot", bot_signals)
        suspicious_signals = _collect(_SUSPICIOUS_WORDING, text)
        if suspicious_signals:
            return _verdict_dict("suspicious", suspicious_signals)
        trust = _trust_percentage(text)
        trust_signals = _collect(_TRUST_PCT, text)
        if trust is not None:
            verdict = "clean" if trust >= 90 else "suspicious"
            return _verdict_dict(verdict, trust_signals)
        clean_signals = _collect(_CLEAN_WORDING, text)
        if clean_signals:
            return _verdict_dict("clean", clean_signals)
        return _verdict_dict("unknown", [])
    except Exception:
        return _verdict_dict("unknown", [])


# Pixelscan renders a result summary page. Defensively look for
# verdict-like phrases and score-like numbers.
_PIXELSCAN_SCORE = r"(?:score|risk|rating)\s*[:\-]?\s*([0-9]{1,3}(?:\.[0-9]+)?)"


def parse_pixelscan(html):
    """Heuristically parse a rendered Pixelscan result page.

    Pixelscan (https://pixelscan.net) shows a result summary after its
    checks run. Its markup changes often, so this scans the rendered text
    for verdict-like phrases rather than DOM structure:

    * explicit bot-detection wording -> 'bot'
    * 'inconsistent'/'suspicious'/'proxy'/'vpn'/'datacenter'-type wording
      -> 'suspicious'
    * a score-like number near 'score'/'risk': interpreted as a *risk*
      score, 0-100; <= 20 -> 'clean' (low risk), >= 80 -> 'suspicious'
      (high risk); the 21-79 band is ambiguous -> 'unknown' unless other
      wording matched
    * clean wording ('consistent', 'no issues detected', ...) -> 'clean'
    * nothing matched -> 'unknown'

    The numeric handling is deliberately conservative: a mid-range score
    with no explicit wording stays 'unknown' rather than guessed.
    Never raises; unmatched or broken input yields 'unknown'.
    """
    try:
        text = html if isinstance(html, str) else ""
    except Exception:
        return _verdict_dict("unknown", [])
    try:
        bot_signals = _collect(_BOT_WORDING, text)
        if bot_signals:
            return _verdict_dict("bot", bot_signals)
        suspicious_signals = _collect(
            r"(?:inconsisten\w*|suspicious|proxy|vpn|datacenter|data\s*center|tor\s+exit|mismatch)",
            text,
        )
        if suspicious_signals:
            return _verdict_dict("suspicious", suspicious_signals)
        score_signals = _collect(_PIXELSCAN_SCORE, text)
        score = None
        try:
            match = re.search(_PIXELSCAN_SCORE, text, re.IGNORECASE)
            if match:
                value = float(match.group(1))
                if 0 <= value <= 100:
                    score = value
        except Exception:
            score = None
        if score is not None:
            # Risk-score reading: a very low number is low risk (clean), a
            # very high number is high risk (suspicious); the middle band
            # is ambiguous and stays 'unknown' rather than guessed.
            if score <= 20:
                return _verdict_dict("clean", score_signals)
            if score >= 80:
                return _verdict_dict("suspicious", score_signals)
            return _verdict_dict("unknown", score_signals)
        clean_signals = _collect(_CLEAN_WORDING, text)
        if clean_signals:
            return _verdict_dict("clean", clean_signals)
        return _verdict_dict("unknown", [])
    except Exception:
        return _verdict_dict("unknown", [])
