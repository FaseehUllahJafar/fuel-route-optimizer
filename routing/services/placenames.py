"""Normalization of US place names, so CSV cities match Census Gazetteer names."""

import re

STATE_CODES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
US_STATES = frozenset(STATE_CODES.values())

# Words the Gazetteer appends to names: "Dallas city", "Abanda CDP",
# "Nashville-Davidson metropolitan government (balance)".
_GAZETTEER_SUFFIX = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|"
    r"unified government|urban county|metro township|charter township|"
    r"city|town|township|village|borough|cdp|ccd|municipality|plantation|"
    r"gore|grant|location|purchase|corporation|comunidad|zona urbana)$"
)

_ABBREVIATIONS = {
    "st": "saint",
    "ste": "sainte",
    "ft": "fort",
    "mt": "mount",
    "pt": "point",
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
}


def normalize_place(name: str) -> str:
    """Lowercase, drop punctuation and Gazetteer suffixes, and expand abbreviations."""
    name = re.sub(r"\(.*?\)", " ", name.lower())
    name = name.replace("'", "")
    name = re.sub(r"[^a-z0-9]+", " ", name).strip()
    # Some names carry two suffixes, e.g. "... metropolitan government (balance)".
    for _ in range(2):
        name = _GAZETTEER_SUFFIX.sub("", name)
    return " ".join(_ABBREVIATIONS.get(word, word) for word in name.split())


def parse_state(token: str) -> str | None:
    """Return the two-letter code for 'TX', 'tx' or 'Texas', or None."""
    token = token.strip().rstrip(".")
    if token.upper() in US_STATES:
        return token.upper()
    return STATE_CODES.get(token.lower())
