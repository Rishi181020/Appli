"""Country and state/province: from the person's profile, matched onto whatever a form's list calls them."""
import re

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico",
}
_ABBR = {v.lower(): k for k, v in US_STATES.items()}

# what forms call the same country
_COUNTRY_ALIASES = {
    "united states": ["United States of America", "United States", "USA", "US", "U.S.", "U.S.A."],
    "united kingdom": ["United Kingdom", "UK", "Great Britain", "United Kingdom of Great Britain and Northern Ireland"],
    "india": ["India"],
    "canada": ["Canada"],
}


def _n(s: str) -> str:
    return re.sub(r"[^a-z ]", "", (s or "").lower()).strip()


def canonical_country(country: str) -> str:
    c = _n(country)
    for key, names in _COUNTRY_ALIASES.items():
        if c == key or c in (_n(x) for x in names):
            return key
    return c


def from_location(location: str) -> tuple[str, str]:
    """'Santa Clara, CA' -> ('California', 'United States'). ('', '') if it can't tell."""
    m = re.search(r",\s*([A-Z]{2})\b", location or "")
    if m and m.group(1) in US_STATES:
        return US_STATES[m.group(1)], "United States"
    return "", ""


def country_option(country: str, options: list[str]) -> str | None:
    """The form's option for this country ('United States' -> 'United States of America'), or None."""
    if not country:
        return None
    key = canonical_country(country)
    names = [_n(x) for x in _COUNTRY_ALIASES.get(key, [country])] + [key]
    for o in options:
        if _n(o) in names:
            return o
    for o in options:  # 'United States of America (+1)' for a phone-code list
        if any(n and _n(o).startswith(n) for n in names):
            return o
    return None


def state_option(state: str, options: list[str]) -> str | None:
    """The form's option for this state: full name or two-letter code, whichever the list uses."""
    if not state:
        return None
    full = US_STATES.get(state.upper(), state)
    cands = {_n(full), _n(_ABBR.get(full.lower(), ""))} - {""}
    for o in options:
        if _n(o) in cands:
            return o
    return None
