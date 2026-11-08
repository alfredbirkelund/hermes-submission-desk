"""Check rules: validation, normalisation and plain-language rendering.

Rules are part of a requirement version, so changing a rule creates a new version and makes
earlier reviews of that requirement stale.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import sniff
from .errors import Invalid
from .words import CONVENTIONS

KINDS = ("document", "text", "human_action", "attestation", "deadline", "other")

WORD_CONVENTIONS = tuple(CONVENTIONS) + ("unresolved",)
_FILE_RULES = {
    "formats", "max_size", "max_bytes", "min_pages", "max_pages", "min_words", "max_words",
    "word_convention", "word_exclusions", "forbid_encryption",
}
_ALLOWED = _FILE_RULES | {"deadline"}
# The rules each kind's checks evaluate. A rule on a kind that never evaluates it would be shown
# but never checked, so it is refused.
_RULES_BY_KIND = {"document": _FILE_RULES, "text": _FILE_RULES, "deadline": {"deadline"},
                  "human_action": set(), "attestation": set(), "other": set()}

_UNITS = {
    "b": (1, 1), "byte": (1, 1), "bytes": (1, 1),
    "kb": (1000, 1024), "k": (1000, 1024),
    "mb": (1000**2, 1024**2), "m": (1000**2, 1024**2),
    "gb": (1000**3, 1024**3), "g": (1000**3, 1024**3),
    "kib": (1024, 1024), "mib": (1024**2, 1024**2), "gib": (1024**3, 1024**3),
}
_SIZE_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*([A-Za-z]+)\s*$")


def _int(value, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise Invalid(f"Rule '{name}' must be a whole number.")
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        raise Invalid(f"Rule '{name}' must be a whole number, got {value!r}.")
    if number < minimum:
        raise Invalid(f"Rule '{name}' must be at least {minimum}.")
    return number


def _amount(raw) -> float:
    text = str(raw).strip()
    if "," in text:
        whole, _, frac = text.partition(",")
        if len(frac) == 3 and "." not in text:
            raise Invalid(f"'{text}' is ambiguous (thousands separator or decimal comma?).",
                          hint="Write it without separators, e.g. 1000 KB or 1.5 MB.")
        text = f"{whole}.{frac}"
    return float(text)


def parse_size(value, basis: str | None = None) -> dict:
    """'5 MB' -> {bytes_low, bytes_high, text, basis}. Ambiguous decimal/binary units stay a range."""
    if isinstance(value, dict):
        if "bytes" in value:
            n = _int(value["bytes"], "max_size.bytes", minimum=1)
            return {"bytes_low": n, "bytes_high": n, "text": f"{n} bytes", "basis": "exact"}
        amount = value.get("value")
        unit = str(value.get("unit", "")).strip()
        basis = value.get("basis", basis)
        text = f"{amount} {unit}"
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        n = _int(value, "max_size", minimum=1)
        return {"bytes_low": n, "bytes_high": n, "text": f"{n} bytes", "basis": "exact"}
    else:
        text = str(value)
        match = _SIZE_RE.match(text)
        if not match:
            raise Invalid(f"Cannot read the size limit {value!r}.", hint="Use forms like '5 MB', '500 KB', '2 MiB' or a byte count.")
        amount, unit = match.group(1), match.group(2)
    try:
        number = _amount(amount)
    except (TypeError, ValueError):
        raise Invalid(f"Cannot read the size limit {value!r}.") from None
    factors = _UNITS.get(unit.lower())
    if factors is None or number <= 0:
        raise Invalid(f"Unknown size unit in {value!r}.", hint="Supported: B, KB, MB, GB, KiB, MiB, GiB.")
    decimal, binary = factors
    if basis not in (None, "", "unresolved", "decimal", "binary"):
        raise Invalid("Size basis must be 'decimal', 'binary' or 'unresolved'.")
    if decimal == binary:
        low = high = int(number * decimal)
        resolved = "exact" if unit.lower() in ("b", "byte", "bytes") else "binary"
    elif basis == "decimal":
        low = high = int(number * decimal)
        resolved = "decimal"
    elif basis == "binary":
        low = high = int(number * binary)
        resolved = "binary"
    else:
        low, high = int(number * decimal), int(number * binary)
        resolved = "unresolved"
    return {"bytes_low": low, "bytes_high": high, "text": text.strip(), "basis": resolved}


def parse_deadline(value) -> dict:
    if not isinstance(value, dict):
        value = {"at": value}
    at = str(value.get("at") or "").strip()
    if not at and value.get("date"):
        at = f"{value['date']}T{value.get('time') or '23:59'}"
    if not at:
        raise Invalid("Deadline needs a date (and ideally a time).")
    try:
        parsed = datetime.fromisoformat(at)
    except ValueError:
        raise Invalid(f"Deadline {at!r} is not an ISO date/time such as 2027-03-15T23:59.") from None
    tz_name = value.get("timezone")
    if tz_name:
        try:
            ZoneInfo(str(tz_name))
        except (ZoneInfoNotFoundError, ValueError):
            raise Invalid(f"Unknown time zone {tz_name!r}.", hint="Use an IANA name such as Europe/Copenhagen or America/New_York.") from None
    delta = parsed.utcoffset() if parsed.tzinfo else None
    offset = delta.total_seconds() / 60 if delta is not None else None
    return {
        "local": parsed.replace(tzinfo=None).isoformat(timespec="minutes"),
        "timezone": str(tz_name) if tz_name else None,
        "utc_offset_minutes": offset,
    }


def normalize(rules: dict | None, kind: str) -> dict:
    if rules is None:
        return {}
    if not isinstance(rules, dict):
        raise Invalid("Rules must be an object.")
    unknown = sorted(set(rules) - _ALLOWED)
    if unknown:
        raise Invalid(f"Unsupported rule keys: {', '.join(unknown)}.",
                      hint=f"Supported: {', '.join(sorted(_ALLOWED))}. Unsupported rules stay in the statement text and are never machine-passed.")
    present = {key for key, value in rules.items() if value not in (None, "", [], {})}
    unchecked = sorted(present - _RULES_BY_KIND.get(kind, set()))
    if unchecked:
        if "deadline" in unchecked:
            raise Invalid("A deadline is its own requirement: add it with kind 'deadline' so it is checked.",
                          hint="Keep this requirement's other rules here.")
        if kind == "deadline":
            raise Invalid(f"A deadline requirement only takes a deadline rule, not {', '.join(unchecked)}.")
        raise Invalid(f"{kind.replace('_', ' ').capitalize()} requirements are confirmed by you, not machine-checked, "
                      f"so they take no rules ({', '.join(unchecked)}).",
                      hint="Keep the condition in the statement; you confirm it when it is true or done.")
    out: dict = {}
    if rules.get("formats") is not None:
        values = rules["formats"]
        if isinstance(values, str):
            values = [v for v in re.split(r"[,/ ]+", values) if v]
        formats, bad = sniff.normalize_formats(list(values))
        if bad:
            raise Invalid(f"Unsupported format names: {', '.join(bad)}.", hint="Supported: pdf, png, jpeg, docx, xlsx, pptx, odt, zip, txt.")
        if formats:
            out["formats"] = formats
    if rules.get("max_size") is not None:
        out["max_size"] = parse_size(rules["max_size"])
    elif rules.get("max_bytes") is not None:
        out["max_size"] = parse_size(_int(rules["max_bytes"], "max_bytes", minimum=1))
    for key in ("min_pages", "max_pages", "min_words", "max_words"):
        if rules.get(key) is not None:
            out[key] = _int(rules[key], key, minimum=0)
    if "min_pages" in out and "max_pages" in out and out["min_pages"] > out["max_pages"]:
        raise Invalid("min_pages is larger than max_pages.")
    if "min_words" in out and "max_words" in out and out["min_words"] > out["max_words"]:
        raise Invalid("min_words is larger than max_words.")
    if "min_words" in out or "max_words" in out:
        convention = rules.get("word_convention") or "unresolved"
        if convention not in WORD_CONVENTIONS:
            raise Invalid(f"Unknown word convention {convention!r}.", hint=f"Use one of: {', '.join(WORD_CONVENTIONS)}.")
        out["word_convention"] = convention
        exclusions = rules.get("word_exclusions") or []
        if isinstance(exclusions, str):
            exclusions = [exclusions]
        out["word_exclusions"] = [str(x).strip() for x in exclusions if str(x).strip()]
    if rules.get("forbid_encryption") is not None:
        out["forbid_encryption"] = bool(rules["forbid_encryption"])
    if rules.get("deadline") is not None:
        out["deadline"] = parse_deadline(rules["deadline"])
    return out


def describe(rules: dict) -> list[str]:
    lines: list[str] = []
    if rules.get("formats"):
        lines.append("Format: " + " or ".join(sniff.LABELS.get(f, f) for f in rules["formats"]))
    size = rules.get("max_size")
    if size:
        if size["basis"] == "unresolved":
            lines.append(
                f"Max size: {size['text']} ({size['bytes_low']:,} bytes if decimal, {size['bytes_high']:,} if binary; "
                "the instructions do not say which)"
            )
        else:
            lines.append(f"Max size: {size['text']} = {size['bytes_low']:,} bytes ({size['basis']})")
    lo, hi = rules.get("min_pages"), rules.get("max_pages")
    if lo is not None or hi is not None:
        lines.append("Pages: " + _range_text(lo, hi))
    lo, hi = rules.get("min_words"), rules.get("max_words")
    if lo is not None or hi is not None:
        convention = rules.get("word_convention", "unresolved")
        conv_text = "counting method not stated" if convention == "unresolved" else convention.replace("_", " ")
        extra = f"; excludes {', '.join(rules['word_exclusions'])}" if rules.get("word_exclusions") else ""
        lines.append(f"Words: {_range_text(lo, hi)} ({conv_text}{extra})")
    if rules.get("forbid_encryption"):
        lines.append("No password protection")
    deadline = rules.get("deadline")
    if deadline:
        zone = deadline.get("timezone") or (
            f"UTC{deadline['utc_offset_minutes'] / 60:+g}" if deadline.get("utc_offset_minutes") is not None else "time zone not stated"
        )
        lines.append(f"Deadline: {deadline['local'].replace('T', ' ')} ({zone})")
    return lines


def _range_text(lo, hi) -> str:
    if lo is not None and hi is not None:
        return f"between {lo} and {hi}"
    if hi is not None:
        return f"at most {hi}"
    return f"at least {lo}"
