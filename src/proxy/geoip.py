"""IP geolocation via MaxMind GeoLite2 (free).

Database: data/GeoLite2-City.mmdb (MaxMind, CC BY-SA 4.0 — attribution
required; see https://www.maxmind.com/en/geolite2/eula)

Used for "Based on IP" fingerprint modes: timezone, location, language.
"""

import os

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))),
    "data", "GeoLite2-City.mmdb")

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        if not os.path.exists(_DB_PATH):
            raise FileNotFoundError(
                "GeoLite2 database not found at %s" % _DB_PATH)
        import geoip2.database
        _reader = geoip2.database.Reader(_DB_PATH)
    return _reader


def lookup(ip):
    """Look up an IP address.

    Returns dict with: country_code, country, city, timezone,
    latitude, longitude. Returns None on any failure (never raises).
    """
    try:
        reader = _get_reader()
        resp = reader.city(ip)
        return {
            "country_code": resp.country.iso_code,
            "country": resp.country.name,
            "city": resp.city.name,
            "timezone": resp.location.time_zone,
            "latitude": resp.location.latitude,
            "longitude": resp.location.longitude,
        }
    except Exception:
        return None


# Minimal country -> language map for "Based on IP" language mode.
_COUNTRY_LANG = {
    "US": "en-US", "GB": "en-GB", "CA": "en-CA", "AU": "en-AU",
    "DE": "de-DE", "FR": "fr-FR", "ES": "es-ES", "IT": "it-IT",
    "NL": "nl-NL", "PT": "pt-PT", "BR": "pt-BR", "MX": "es-MX",
    "JP": "ja-JP", "KR": "ko-KR", "CN": "zh-CN", "TW": "zh-TW",
    "RU": "ru-RU", "UA": "uk-UA", "PL": "pl-PL", "SE": "sv-SE",
    "NO": "nb-NO", "DK": "da-DK", "FI": "fi-FI", "IN": "en-IN",
    "PK": "en-PK", "AE": "ar-AE", "SA": "ar-SA", "TR": "tr-TR",
}


def language_for_country(country_code):
    """Return a language tag for a country code (e.g. 'US' -> 'en-US')."""
    if not country_code:
        return "en-US"
    return _COUNTRY_LANG.get(country_code.upper(), "en-US")
