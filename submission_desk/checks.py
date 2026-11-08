"""Machine checks on the exact bytes that were read.

Every result names the check, its status (pass | fail | unknown | not_applicable), the
measurement, the limit, the units, the method and whether it is an estimate. An omitted checker
never produces a pass: a rule we cannot evaluate yields ``unknown`` with the reason.
"""

from __future__ import annotations

from . import ENGINE_VERSION, pdfprobe, sniff, words
from .files import Problem, Snapshot

PASS, FAIL, UNKNOWN, NA = "pass", "fail", "unknown", "not_applicable"


def _result(check: str, status: str, *, measurement=None, limit=None, units=None, method="", detail="",
            estimate=False, transient=False) -> dict:
    return {
        "check": check, "status": status, "measurement": measurement, "limit": limit, "units": units,
        "method": method, "detail": detail, "estimate": estimate, "transient": transient,
        "engine": ENGINE_VERSION,
    }


class PdfFacts:
    """One probe per file revision, shared by the page, word and encryption checks."""

    def __init__(self, data: bytes, want_text: bool, probe=pdfprobe.run_probe):
        self.result = probe(data, want_text=want_text)

    @property
    def status(self) -> str:
        return str(self.result.get("status"))

    @property
    def engine(self) -> str:
        return str(self.result.get("engine") or "pypdf")


def run(read: Snapshot | Problem, rules: dict, *, probe=pdfprobe.run_probe) -> list[dict]:
    if isinstance(read, Problem):
        return [_presence_problem(read)]
    results = [_result("presence", PASS, measurement={"rel_path": read.rel_path, "bytes": read.size},
                       method="Opened and read in full (no symlinks followed outside the folder).")]
    detected = sniff.sniff(read.data, size=read.size)
    fmt = detected["format"]
    if rules.get("formats"):
        results.append(_format_check(read, detected, rules["formats"]))
    if rules.get("max_size"):
        results.append(_size_check(read.size, rules["max_size"]))
    wants_pages = rules.get("min_pages") is not None or rules.get("max_pages") is not None
    wants_words = rules.get("min_words") is not None or rules.get("max_words") is not None
    wants_enc = bool(rules.get("forbid_encryption"))
    facts = None
    if fmt == "pdf" and read.data is not None and (wants_pages or wants_words or wants_enc):
        facts = PdfFacts(read.data, want_text=wants_words, probe=probe)
    if wants_pages:
        results.append(_pages_check(fmt, read, facts, rules))
    if wants_words:
        results.append(_words_check(fmt, read, facts, rules))
    if wants_enc:
        results.append(_encryption_check(fmt, facts))
    return results


def _presence_problem(problem: Problem) -> dict:
    status = {
        "missing": FAIL, "root_missing": FAIL, "outside_root": FAIL, "invalid_path": FAIL,
        "not_regular": FAIL, "moved_ambiguous": FAIL,
    }.get(problem.status, UNKNOWN)
    return _result("presence", status, measurement={"rel_path": problem.rel_path, "problem": problem.status},
                   method="Open the mapped path inside the material folder.", detail=problem.detail,
                   transient=problem.status == "changed_during_read")


def _format_check(read: Snapshot, detected: dict, allowed: list[str]) -> dict:
    fmt = detected["format"]
    labels = " or ".join(sniff.LABELS.get(f, f) for f in allowed)
    method = "Content signature (magic bytes / package structure); the file name is ignored."
    if read.data is None:
        return _result("format", UNKNOWN, measurement={"detected": None}, limit=allowed, method=method,
                       detail="File too large to inspect its content.")
    suffix = read.rel_path.rsplit(".", 1)[-1].lower() if "." in read.rel_path else ""
    named = sniff.FORMAT_ALIASES.get(suffix)
    note = ""
    if named and named != fmt and fmt not in ("unknown", "empty"):
        note = f" The name ends in .{suffix} but the content is {sniff.LABELS.get(fmt, fmt)}."
    elif named and fmt in ("unknown", "empty"):
        note = f" The name ends in .{suffix}, which does not prove the format."
    if fmt in allowed:
        return _result("format", PASS, measurement={"detected": fmt}, limit=allowed, method=method,
                       detail=detected["detail"] + note)
    return _result("format", FAIL, measurement={"detected": fmt}, limit=allowed, method=method,
                   detail=f"Required {labels}; content is {sniff.LABELS.get(fmt, fmt)}. {detected['detail']}{note}".strip())


def _size_check(size: int, limit: dict) -> dict:
    method = "Exact byte count of the file as read."
    low, high = limit["bytes_low"], limit["bytes_high"]
    if size <= low:
        return _result("size", PASS, measurement=size, limit=limit, units="bytes", method=method,
                       detail=f"{size:,} bytes, within {limit['text']}.")
    if size > high:
        return _result("size", FAIL, measurement=size, limit=limit, units="bytes", method=method,
                       detail=f"{size:,} bytes exceeds {limit['text']} under any reading ({high:,} bytes).")
    return _result("size", UNKNOWN, measurement=size, limit=limit, units="bytes", method=method,
                   detail=(f"{size:,} bytes passes if {limit['text']} means {high:,} bytes (binary) but fails if it "
                           f"means {low:,} bytes (decimal). The instructions do not say which; ask the institution "
                           "or make the file smaller than the decimal limit."))


def _probe_unknown(check: str, facts: PdfFacts | None, limit, units: str) -> dict | None:
    if facts is None:
        return _result(check, UNKNOWN, limit=limit, units=units, detail="PDF content was not inspected.")
    status = facts.status
    if status == "ok":
        return None
    messages = {
        "encrypted_locked": "The PDF is password-protected; it cannot be read without the password.",
        "parse_error": "The PDF could not be parsed (damaged or non-standard). This is not the same as 0.",
        "engine_unavailable": "The PDF engine (pypdf) is not installed in this Hermes environment.",
        "timeout": "PDF inspection timed out and was stopped.",
        "crashed": "The PDF inspector failed.",
    }
    detail = messages.get(status, status)
    if facts.result.get("detail"):
        detail = f"{detail} ({facts.result['detail']})"
    return _result(check, UNKNOWN, limit=limit, units=units, method=facts.engine, detail=detail,
                   transient=status in ("timeout", "crashed"))


def _pages_check(fmt: str, read: Snapshot, facts: PdfFacts | None, rules: dict) -> dict:
    limit = {"min": rules.get("min_pages"), "max": rules.get("max_pages")}
    if fmt != "pdf":
        return _result("pages", UNKNOWN, limit=limit, units="pages",
                       detail=f"Page counts are only supported for PDF; this file is {sniff.LABELS.get(fmt, fmt)}.")
    problem = _probe_unknown("pages", facts, limit, "pages")
    if problem:
        return problem
    assert facts is not None
    pages = facts.result.get("pages")
    method = f"PDF page tree count ({facts.engine})."
    if not isinstance(pages, int) or pages <= 0:
        return _result("pages", UNKNOWN, measurement=pages, limit=limit, units="pages", method=method,
                       detail="The page tree is empty or unreadable; not treated as 0 pages.")
    ok = (limit["min"] is None or pages >= limit["min"]) and (limit["max"] is None or pages <= limit["max"])
    note = " (opens without a password but is encrypted)" if facts.result.get("encrypted") else ""
    return _result("pages", PASS if ok else FAIL, measurement=pages, limit=limit, units="pages", method=method,
                   detail=f"{pages} page{'s' if pages != 1 else ''}{note}.")


def _within(count: int, limit: dict) -> bool:
    return (limit["min"] is None or count >= limit["min"]) and (limit["max"] is None or count <= limit["max"])


def _words_check(fmt: str, read: Snapshot, facts: PdfFacts | None, rules: dict) -> dict:
    convention = rules.get("word_convention", "unresolved")
    exclusions = rules.get("word_exclusions") or []
    limit = {"min": rules.get("min_words"), "max": rules.get("max_words"), "convention": convention,
             "exclusions": exclusions}
    if fmt == "text":
        text = sniff.decode_text(read.data or b"")
        source = "UTF-8 text file"
        estimate_reasons: list[str] = []
    elif fmt == "pdf":
        problem = _probe_unknown("words", facts, limit, "words")
        if problem:
            return problem
        assert facts is not None
        text = "\n".join(facts.result.get("texts") or [])
        source = f"PDF text extracted with {facts.engine}"
        estimate_reasons = ["PDF text extraction can drop, merge or repeat text (headers, footers, columns, images)."]
        if not text.strip():
            return _result("words", UNKNOWN, measurement=0, limit=limit, units="words", method=source,
                           detail="No extractable text (scanned or image-only PDF); not treated as 0 words.")
        if facts.result.get("text_truncated"):
            estimate_reasons.append("Extracted text was truncated.")
    else:
        return _result("words", UNKNOWN, limit=limit, units="words",
                       detail=f"Word counts are supported for plain text and PDF only; this file is {sniff.LABELS.get(fmt, fmt)}.")
    conventions = [convention] if convention != "unresolved" else list(words.CONVENTIONS)
    counts = {c: words.count_words(text, c) for c in conventions}
    unsupported = next((v["unsupported"] for v in counts.values() if v["unsupported"]), None)
    if unsupported:
        return _result("words", UNKNOWN, limit=limit, units="words", method=source, detail=unsupported)
    if convention == "unresolved":
        estimate_reasons.append("The instructions do not say how words are counted; both supported conventions shown.")
    if exclusions:
        estimate_reasons.append(f"The rule excludes {', '.join(exclusions)}; this counter cannot separate those parts, so they are included.")
    values = sorted({v["count"] for v in counts.values()})
    measurement = {c: v["count"] for c, v in counts.items()}
    shown = f"{values[0]}" if len(values) == 1 else f"{values[0]}-{values[-1]}"
    method = f"{source}; " + "; ".join(f"{c}: {words.CONVENTIONS[c]}" for c in conventions)
    if not estimate_reasons:
        ok = _within(values[0], limit)
        return _result("words", PASS if ok else FAIL, measurement=measurement, limit=limit, units="words",
                       method=method, detail=f"{shown} words ({conventions[0].replace('_', ' ')}).")
    over = limit["max"] is not None and values[0] > limit["max"]
    under = limit["min"] is not None and values[-1] < limit["min"]
    if over or under:
        return _result("words", FAIL, measurement=measurement, limit=limit, units="words", method=method,
                       detail=f"Estimated {shown} words, outside the limit. " + " ".join(estimate_reasons), estimate=True)
    return _result("words", UNKNOWN, measurement=measurement, limit=limit, units="words", method=method,
                   detail=f"Estimated {shown} words; not certified. " + " ".join(estimate_reasons)
                   + " Check the count with the institution's method and record it.", estimate=True)


def _encryption_check(fmt: str, facts: PdfFacts | None) -> dict:
    method = "PDF encryption dictionary (pypdf)."
    if fmt != "pdf":
        return _result("encryption", UNKNOWN, detail="The password-protection check supports PDF only.")
    if facts is None:
        return _result("encryption", UNKNOWN, detail="PDF content was not inspected.")
    encrypted = facts.result.get("encrypted")
    if encrypted is None:
        problem = _probe_unknown("encryption", facts, None, "")
        return problem or _result("encryption", UNKNOWN, method=method, detail="Encryption state unknown.")
    if encrypted:
        how = "opens without a password" if facts.result.get("opens_without_password") else "needs a password"
        return _result("encryption", FAIL, measurement={"encrypted": True}, method=method,
                       detail=f"The PDF is encrypted ({how}); portals often reject protected files.")
    return _result("encryption", PASS, measurement={"encrypted": False}, method=method, detail="Not encrypted.")
