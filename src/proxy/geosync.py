"""Proxy geo-sync: align a profile persona's locale/timezone/geo with the proxy.

Best-effort helpers that suggest timezone, locale and geolocation for a
profile persona based on the proxy's country. Network operations never
raise: every failure is caught and returned as a human-readable note.

IMPORTANT LIMITATION
--------------------
:func:`sync_persona_to_ip` performs a *direct* HTTPS lookup (no proxy
tunnelling). A direct request resolves THIS machine's egress IP, not the
proxy's egress IP, so the returned country is only correct when the proxy
exits in the same country as this machine (or when the lookup is run on a
host whose traffic already egresses through the proxy). True proxy-egress
resolution requires routing the lookup through the proxy itself (e.g.
``urllib`` with a ``ProxyHandler`` or a SOCKS tunnel) and is documented
future work -- it is deliberately NOT implemented here.
"""

import src._vendor  # noqa: F401  (first import: keep stdlib + venv only after this)

import copy
import json
import urllib.request

# Compact tables: top ~25 countries by proxy usage. Keep both dicts keyed by
# the same ISO-3166-1 alpha-2 codes and consistent with each other.
COUNTRY_TIMEZONES = {
    "US": "America/New_York",
    "GB": "Europe/London",
    "DE": "Europe/Berlin",
    "CA": "America/Toronto",
    "AU": "Australia/Sydney",
    "FR": "Europe/Paris",
    "NL": "Europe/Amsterdam",
    "SG": "Asia/Singapore",
    "AE": "Asia/Dubai",
    "IN": "Asia/Kolkata",
    "BR": "America/Sao_Paulo",
    "ES": "Europe/Madrid",
    "IT": "Europe/Rome",
    "SE": "Europe/Stockholm",
    "CH": "Europe/Zurich",
    "AT": "Europe/Vienna",
    "BE": "Europe/Brussels",
    "IE": "Europe/Dublin",
    "NZ": "Pacific/Auckland",
    "MX": "America/Mexico_City",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "PL": "Europe/Warsaw",
    "NO": "Europe/Oslo",
    "DK": "Europe/Copenhagen",
}

COUNTRY_LOCALES = {
    "US": "en-US",
    "GB": "en-GB",
    "DE": "de-DE",
    "CA": "en-CA",
    "AU": "en-AU",
    "FR": "fr-FR",
    "NL": "nl-NL",
    "SG": "en-SG",
    "AE": "ar-AE",
    "IN": "en-IN",
    "BR": "pt-BR",
    "ES": "es-ES",
    "IT": "it-IT",
    "SE": "sv-SE",
    "CH": "de-CH",
    "AT": "de-AT",
    "BE": "nl-BE",
    "IE": "en-IE",
    "NZ": "en-NZ",
    "MX": "es-MX",
    "JP": "ja-JP",
    "KR": "ko-KR",
    "PL": "pl-PL",
    "NO": "nb-NO",
    "DK": "da-DK",
}

# Reverse lookup: IANA timezone -> country code (first country wins).
_TIMEZONE_TO_COUNTRY = {}
for _cc, _tz in COUNTRY_TIMEZONES.items():
    _TIMEZONE_TO_COUNTRY.setdefault(_tz, _cc)

_GEO_API_URL = (
    "https://ip-api.com/json/?fields=status,countryCode,lat,lon"
)
_GEO_TIMEOUT = 6  # seconds


def suggest_timezone(country_code):
    """Return the primary IANA timezone for ``country_code``, or None.

    Args:
        country_code: ISO-3166-1 alpha-2 code (e.g. ``'DE'``).
    """
    if not country_code:
        return None
    return COUNTRY_TIMEZONES.get(str(country_code).strip().upper())


def suggest_locale(country_code):
    """Return the locale for ``country_code`` (e.g. ``'de-DE'``), or None."""
    if not country_code:
        return None
    return COUNTRY_LOCALES.get(str(country_code).strip().upper())


def country_from_timezone(tz):
    """Best-guess country code for an IANA timezone, or None."""
    if not tz:
        return None
    return _TIMEZONE_TO_COUNTRY.get(str(tz).strip())


def _lookup_geo():
    """Direct ip-api.com lookup of this machine's egress IP.

    Returns:
        ``(country_code, lat, lon)`` on success.

    Raises:
        Exception: any network, HTTP, JSON or payload problem. Callers
            catch everything; this helper never swallows errors itself.
    """
    req = urllib.request.Request(
        _GEO_API_URL,
        headers={"User-Agent": "antidetect-browser-geosync/1.0"},
    )
    with urllib.request.urlopen(req, timeout=_GEO_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("status") != "success" or not payload.get("countryCode"):
        raise ValueError(
            "geo API returned no country for this IP: %r" % (payload,)
        )
    return (
        str(payload["countryCode"]).upper(),
        float(payload["lat"]),
        float(payload["lon"]),
    )


def sync_persona_to_ip(persona):
    """Best-effort sync of a persona's timezone/locale/geolocation.

    IMPORTANT LIMITATION (see module docstring): the geo lookup is a direct
    request, so it resolves THIS machine's egress IP, not the proxy's.
    True proxy-egress resolution (routing the lookup through the proxy)
    is future work.

    Args:
        persona: a profile persona dict. Never mutated in place; the
            returned persona is a copy.

    Returns:
        ``(persona_copy, note)`` where ``note`` is a short human-readable
        status string (``'no proxy — nothing to sync'``,
        ``'geo lookup failed: <reason>'``, or ``'synced to <CC>'``).

    Never raises.
    """
    synced = copy.deepcopy(persona)
    if not synced.get("proxy"):
        return synced, "no proxy — nothing to sync"
    try:
        country_code, lat, lon = _lookup_geo()
    except Exception as exc:  # noqa: BLE001 -- best-effort by contract
        return synced, "geo lookup failed: %s" % (exc,)
    tz = suggest_timezone(country_code)
    locale = suggest_locale(country_code)
    if tz is not None:
        synced["timezone"] = tz
    if locale is not None:
        synced["locale"] = locale
    if tz is not None and locale is not None:
        synced["geolocation"] = {"latitude": lat, "longitude": lon}
    return synced, "synced to %s" % (country_code,)


class GeoSync:
    """Namespace class exposing the geo-sync helpers as static methods."""

    suggest_timezone = staticmethod(suggest_timezone)
    suggest_locale = staticmethod(suggest_locale)
    country_from_timezone = staticmethod(country_from_timezone)
    sync_persona_to_ip = staticmethod(sync_persona_to_ip)
