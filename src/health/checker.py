"""Profile health checker: consistency validation + best-effort network/detection probes."""

import src._vendor  # noqa: F401  (must stay first: keeps vendored deps importable)

import re
import threading

_IPv4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
_NETWORK_BUDGET_S = 25
_GOTO_TIMEOUT_MS = 20000
_IP_CHECK_URL = "https://browserleaks.com/ip"

# Detection harness: third-party bot/fingerprint test sites. Each leg is
# best-effort inside its own thread budget; parsing is heuristic (see
# src/health/detectors.py) because these pages are JS-heavy and their
# markup changes often.
_DETECTION_BUDGET_S = 20
_DETECTION_GOTO_TIMEOUT_MS = 15000
_DETECTION_SITES = (
    ("creepjs", "https://abrahamjuliot.github.io/creepjs/", "parse_creepjs"),
    ("pixelscan", "https://pixelscan.net/", "parse_pixelscan"),
)

# verdict -> weight used in the detection part of the score.
_VERDICT_WEIGHTS = {
    "clean": 1.0,
    "unknown": 0.5,
    "suspicious": 0.25,
    "bot": 0.0,
}
# Neutral detection part when no site produced a parseable verdict.
_DETECTION_NEUTRAL = 15.0

_SKIP_REASON = ("camoufox browser binary not installed; "
                "run setup_browser.py first")


def _extract_ipv4(text):
    """Return the first syntactically valid IPv4 address in text, else None."""
    for match in _IPv4_RE.finditer(text or ""):
        if all(0 <= int(octet) <= 255 for octet in match.group(1).split(".")):
            return match.group(1)
    return None


class HealthChecker:
    """Checks a profile persona for fingerprint consistency and network health.

    The consistency part always runs via the engine-aware
    :func:`src.fingerprints.validator.validate` (dispatches on the
    profile's ``engine`` field).
    The network part is best-effort: a headless launch + one page visit inside
    a hard overall timeout. Any failure there is recorded, never raised.
    The detection part visits third-party bot/fingerprint test sites
    (CreepJS, Pixelscan) in the same best-effort pattern and parses the
    rendered pages heuristically; failures are recorded, never raised.
    """

    network_budget_s = _NETWORK_BUDGET_S
    detection_budget_s = _DETECTION_BUDGET_S

    def check(self, persona):
        """Run all health checks for a persona dict.

        Returns {'profile', 'consistency', 'network', 'detection', 'score'}.

        Score formula (0-100, 1 decimal):
            consistency_part = 60 * (passed / total consistency checks)
            detection_part   = 30 * (average over attempted-and-parsed
                               detection sites of: clean=1.0, unknown=0.5,
                               suspicious=0.25, bot=0.0; sites skipped or
                               errored are EXCLUDED from the average; if no
                               site produced a parseable verdict,
                               detection_part = 15.0 neutral)
            network_part     = 10 if network status == 'ok' else 0
            score = round(consistency_part + detection_part + network_part,
                          1), floored at 0.
        """
        results = self._run_consistency(persona)
        total = len(results)
        passed = sum(1 for r in results if isinstance(r, dict) and r.get("passed"))
        failed = total - passed
        consistency = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "failures": [r for r in results
                         if not (isinstance(r, dict) and r.get("passed"))],
        }
        network = self._run_network_check(persona)
        detection = self._run_detection_check(persona)
        wire = self._run_wire_checks(persona)
        score = self._compute_score(consistency, network, detection)
        name = persona.get("name") if isinstance(persona, dict) else None
        return {
            "profile": name,
            "consistency": consistency,
            "network": network,
            "detection": detection,
            "wire": wire,
            "score": score,
        }

    @staticmethod
    def _compute_score(consistency, network, detection):
        """Score 0-100 (1 decimal) per the formula in check()'s docstring."""
        total = consistency.get("total", 0) or 0
        passed = consistency.get("passed", 0) or 0
        consistency_part = 60.0 * passed / total if total else 0.0
        weights = []
        if isinstance(detection, dict):
            for key in ("creepjs", "pixelscan"):
                leg = detection.get(key)
                if isinstance(leg, dict) and leg.get("status") == "ok":
                    weight = _VERDICT_WEIGHTS.get(leg.get("verdict"))
                    if weight is not None:
                        weights.append(weight)
        detection_part = (30.0 * sum(weights) / len(weights)
                          if weights else _DETECTION_NEUTRAL)
        network_part = 10.0 if network.get("status") == "ok" else 0.0
        return round(max(0.0, consistency_part + detection_part
                         + network_part), 1)

    @staticmethod
    def _run_wire_checks(persona):
        """DNS-leak + TLS-profile wire checks (Worker B integration).

        Grades the wire, not the config: confirms DNS exits via the proxy
        and our TLS handshake matches the engine's browser profile.
        Fail-closed and exception-safe: a wire problem never crashes the
        health check; results are informational (not in the score).
        """
        try:
            from src.fingerprint import wire_checks as wc
        except Exception as exc:
            return [{"check": "wire-available", "passed": False,
                     "detail": "wire_checks unavailable: %s" % exc}]
        proxy = persona.get("proxy") if isinstance(persona, dict) else None
        engine = persona.get("engine") if isinstance(persona, dict) else None
        out = []
        # Short timeouts: health checks must stay snappy in the dashboard.
        try:
            out.append(wc.dns_leak_check(proxy, timeout=8))
        except Exception as exc:
            out.append({"check": "dns-leak", "passed": False,
                        "detail": "error: %s" % exc})
        try:
            out.append(wc.tls_profile_check(
                proxy, profile=wc.profile_for_engine(engine), timeout=10))
        except Exception as exc:
            out.append({"check": "tls-profile", "passed": False,
                        "detail": "error: %s" % exc})
        return out

    def _run_consistency(self, persona):
        """Run the engine-aware validator; synthesize a failure if unavailable.

        :func:`src.fingerprints.validator.validate` dispatches on
        ``persona['engine']``: the Chromium check set for patchright
        profiles, the 23 Firefox checks otherwise.
        """
        try:
            from src.fingerprints.validator import validate
        except Exception as exc:
            return [{"check": "validator-available", "passed": False,
                     "detail": "validator module unavailable: %s" % exc}]
        try:
            results = validate(persona)
        except Exception as exc:
            return [{"check": "validator-run", "passed": False,
                     "detail": "validator raised: %s" % exc}]
        if not isinstance(results, list):
            return [{"check": "validator-result", "passed": False,
                     "detail": "validator returned a non-list result"}]
        return results

    def _run_network_check(self, persona):
        """Best-effort headless launch + IP page visit. Never raises."""
        try:
            from src.browser.launcher import launch_profile, browser_binary_present
        except Exception as exc:
            return {"status": "skipped",
                    "reason": ("launcher unavailable: %s" % exc)[:200]}
        try:
            if not browser_binary_present():
                return {"status": "skipped",
                        "reason": "camoufox browser binary not installed; "
                                  "run setup_browser.py first"}
        except Exception as exc:
            return {"status": "skipped",
                    "reason": ("binary check failed: %s" % exc)[:200]}
        outcome = {}
        worker = threading.Thread(
            target=self._probe_browser,
            args=(launch_profile, persona, outcome),
            daemon=True,
        )
        worker.start()
        worker.join(timeout=self.network_budget_s)
        if worker.is_alive():
            return {"status": "error",
                    "reason": "network check timed out after %ss"
                              % self.network_budget_s}
        return outcome.get("result",
                           {"status": "error",
                            "reason": "network check produced no result"})

    def _run_detection_check(self, persona):
        """Best-effort detection-site visits + heuristic parsing. Never raises.

        Returns {'creepjs': {...}, 'pixelscan': {...}} where each leg is
        {'status': 'ok'|'skipped'|'error', 'verdict': ..., 'signals': [...]}
        on success or {'status': ..., 'reason': ...} otherwise.
        """
        legs = {}
        try:
            from src.browser.launcher import launch_profile, browser_binary_present
        except Exception as exc:
            reason = ("launcher unavailable: %s" % exc)[:200]
            for name, _url, _parser in _DETECTION_SITES:
                legs[name] = {"status": "skipped", "reason": reason}
            return legs
        try:
            binary_ok = browser_binary_present()
        except Exception as exc:
            reason = ("binary check failed: %s" % exc)[:200]
            for name, _url, _parser in _DETECTION_SITES:
                legs[name] = {"status": "skipped", "reason": reason}
            return legs
        if not binary_ok:
            for name, _url, _parser in _DETECTION_SITES:
                legs[name] = {"status": "skipped", "reason": _SKIP_REASON}
            return legs
        for name, url, parser_name in _DETECTION_SITES:
            legs[name] = self._run_detection_leg(
                launch_profile, persona, url, parser_name)
        return legs

    def _run_detection_leg(self, launch_profile, persona, url, parser_name):
        """Run one detection-site leg inside a hard thread budget. Never raises."""
        outcome = {}
        try:
            from src.health.detectors import parse_creepjs, parse_pixelscan
            parsers = {"parse_creepjs": parse_creepjs,
                       "parse_pixelscan": parse_pixelscan}
            parser = parsers[parser_name]
        except Exception as exc:
            return {"status": "error",
                    "reason": ("detector unavailable: %s" % exc)[:200]}
        worker = threading.Thread(
            target=self._probe_detection_site,
            args=(launch_profile, persona, url, parser, outcome),
            daemon=True,
        )
        worker.start()
        worker.join(timeout=self.detection_budget_s)
        if worker.is_alive():
            return {"status": "error",
                    "reason": "detection check timed out after %ss"
                              % self.detection_budget_s}
        return outcome.get("result",
                           {"status": "error",
                            "reason": "detection check produced no result"})

    @staticmethod
    def _probe_detection_site(launch_profile, persona, url, parser, outcome):
        """Worker body: launch headless, visit the site, parse content."""
        browser = None
        try:
            browser = launch_profile(persona, headless=True)
            page = browser.page
            page.goto(url, timeout=_DETECTION_GOTO_TIMEOUT_MS)
            html = page.content()
            parsed = parser(html)
            outcome["result"] = {"status": "ok"}
            if isinstance(parsed, dict):
                outcome["result"]["verdict"] = parsed.get("verdict", "unknown")
                outcome["result"]["signals"] = parsed.get("signals", [])
            else:
                outcome["result"]["verdict"] = "unknown"
                outcome["result"]["signals"] = []
        except Exception as exc:
            outcome["result"] = {"status": "error",
                                 "reason": str(exc)[:200]}
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

    @staticmethod
    def _probe_browser(launch_profile, persona, outcome):
        """Worker body: launch headless, visit the IP page, record the result."""
        browser = None
        try:
            browser = launch_profile(persona, headless=True)
            page = browser.page
            page.goto(_IP_CHECK_URL, timeout=_GOTO_TIMEOUT_MS)
            html = page.content()
            outcome["result"] = {
                "status": "ok",
                "ip": _extract_ipv4(html),
                "page_reached": True,
            }
        except Exception as exc:
            outcome["result"] = {"status": "error",
                                 "reason": str(exc)[:200]}
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
