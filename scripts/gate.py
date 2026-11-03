#!/usr/bin/env python3
"""Quality gate for the Charlotte municipal CRM case study.

One command, one answer: green or red. Every check runs against the
repository as it stands on disk, prints a line in a table, and the script
exits non-zero if any check fails. The thresholds and the reasoning behind
each one live in ``QUALITY_BAR.md``; this file is the executable form of
that document.

The gate is standard library only. It needs no network, no credentials, and
no model provider, so it can run in CI on a fresh checkout.

Checks
------

G1  required files present
G2  no commercial terms (pricing, licensing, competitor vendor names)
G3  no personal data (email, phone, social security number)
G4  README contains every required section
G5  mermaid blocks are well formed
G6  no secrets (keys, tokens, private key material)
G7  the numbers published in the README match ``src/roi.py``
G8  prose obeys the house style (no marketing vocabulary, no em dashes)
G9  no traceback was produced by any earlier check

Scope of the scans
------------------

Every file in the repository is scanned for personal data (G3) and for
credential material (G6). The commercial-term scan (G2) and the house-style
scan (G8) skip exactly one file, ``scripts/gate.py``, because that file
necessarily contains the literal strings those two checks look for. There
is no other exemption. The test suite is scanned like any other file, which
is why the test fixtures assemble their sensitive literals at runtime.

Usage
-----

    python3 scripts/gate.py            # full gate, tabulated
    python3 scripts/gate.py --quiet    # one-line summary
    python3 scripts/gate.py --only G4  # run a single check

Exit codes
----------

    0   every check passed
    1   at least one check failed
    2   the gate itself could not run (missing repository, unreadable file)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import traceback
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Files that must exist for the repository to be complete.
REQUIRED_FILES: tuple[str, ...] = (
    "README.md",
    "ARCHITECTURE.md",
    "EVALUATION.md",
    "QUALITY_BAR.md",
    "LICENSE",
    ".gitignore",
    ".github/workflows/ci.yml",
    "scripts/__init__.py",
    "scripts/gate.py",
    "src/__init__.py",
    "src/roi.py",
    "tests/__init__.py",
    "tests/conftest.py",
    "tests/test_roi.py",
    "tests/test_gate.py",
)

#: Headings the README must contain. Reviewers expect a case study to state
#: what it is, what was cut, what is not built, and how to check the claims.
REQUIRED_README_SECTIONS: tuple[str, ...] = (
    "What this is",
    "What we cut, and why",
    "Quickstart",
    "Architecture",
    "The 311 voice path",
    "Compliance posture",
    "Degraded mode and DR",
    "Known limitations",
    "Production roadmap",
    "Repository layout",
    "Safety",
)

#: The one file skipped by the scans that look for the very strings it
#: contains: the commercial-term scan and the house-style scan. The
#: personal-data and secret scans do not skip it.
STYLE_SCAN_EXCLUSIONS: frozenset[Path] = frozenset({REPO / "scripts" / "gate.py"})

#: Directories never scanned.
SCAN_SKIP_DIRS: frozenset[str] = frozenset(
    {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".mypy_cache"}
)

#: Text file suffixes that are scanned.
SCAN_SUFFIXES: frozenset[str] = frozenset(
    {".md", ".py", ".yml", ".yaml", ".txt", ".cfg", ".toml", ".json"}
)

#: Suffix-free files that are scanned when they carry text.
SCAN_NAMES: frozenset[str] = frozenset({"LICENSE", ".gitignore"})

#: A region of a document where sensitive literals are intentionally written
#: down (for example, the list of banned vocabulary in QUALITY_BAR.md). The
#: region is stripped before the style and commercial scans run, so a
#: document can explain a rule without breaking it.
ALLOWLIST_MARKER = re.compile(
    r"<!--\s*gate:allowlist-(?P<kind>start|end)\s*-->"
)

#: Kept for callers that want to assert on the pattern itself.
ALLOWLIST_REGION = re.compile(
    r"<!--\s*gate:allowlist-start\s*-->(.*?)<!--\s*gate:allowlist-end\s*-->",
    re.DOTALL,
)

#: Commercial markers that must not appear in a published file. The first
#: element of each tuple is a human-readable label; the second is the
#: regular expression that detects it.
FORBIDDEN_COMMERCIAL: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("currency amount", re.compile(r"\$\s?\d|\b\d[\d,.]*\s?(USD|dollars)\b", re.IGNORECASE)),
    ("per-seat licensing", re.compile(r"\bper[- ]seat\b", re.IGNORECASE)),
    ("seat price", re.compile(r"\bseat (price|pricing|license|licence|cost)\b", re.IGNORECASE)),
    ("pricing tier", re.compile(r"\bpricing tiers?\b", re.IGNORECASE)),
    ("price list", re.compile(r"\b(price list|list price)\b", re.IGNORECASE)),
    (
        "total cost of ownership",
        re.compile(r"\b(total cost of ownership|TCO)\b", re.IGNORECASE),
    ),
    ("payback period", re.compile(r"\bpayback period\b", re.IGNORECASE)),
    ("cost estimate", re.compile(r"\bcost (estimate|framework|model)\b", re.IGNORECASE)),
    (
        "annual contract value",
        re.compile(r"\b(annual contract value|ACV)\b", re.IGNORECASE),
    ),
    (
        "fee schedule",
        re.compile(
            r"\b(implementation fee|subscription fee|professional services fee|seat fee)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "commercial figure",
        re.compile(
            r"\b(bid total|bid price|bid amount|bid value|our bid|price point)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "competitor vendor",
        re.compile(
            r"\b(salesforce|service ?now|oracle|accenture|deloitte|kpmg|pwc|capgemini|cognizant|epam)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "competitor product",
        re.compile(r"\b(einstein|now assist|microsoft dynamics|copilot)\b", re.IGNORECASE),
    ),
)

#: Personal data that must not appear anywhere in the repository.
PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email address", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    (
        "phone number",
        re.compile(r"(?<![\d-])(?:\+1[ .-]?)?\(?\b\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\d-])"),
    ),
    ("social security number", re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")),
)

#: Secret material that must not appear anywhere in the repository.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai style key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "assigned secret",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*[\"']?[A-Za-z0-9/+=_\-]{16,}"
        ),
    ),
)

#: Marketing vocabulary the house style forbids. These are the words that
#: make a technical document read like a brochure.
BANNED_VOCABULARY: tuple[str, ...] = (
    "revolutionary",
    "game-changing",
    "cutting-edge",
    "seamless",
    "world-class",
    "leverage",
    "synergy",
    "empower",
    "unlock",
    "delight",
    "robust",
    "best-in-class",
)

#: The em dash. House style uses commas, colons, or parentheses instead.
EM_DASH = "\u2014"

#: Mermaid diagram keywords that may open a diagram block.
MERMAID_HEADERS: frozenset[str] = frozenset(
    {
        "flowchart",
        "graph",
        "sequenceDiagram",
        "stateDiagram",
        "stateDiagram-v2",
        "classDiagram",
        "erDiagram",
        "journey",
        "gantt",
        "pie",
        "mindmap",
        "timeline",
        "gitGraph",
    }
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One rule violation, located in one file."""

    rule: str
    path: str
    line: int
    excerpt: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.excerpt}"


def strip_allowlist_regions(text: str) -> str:
    """Remove regions of a document where sensitive literals are declared.

    The region markers nest, because a test file has to spell out the
    marker syntax in order to test it. A flat non-greedy match would end
    the outer region at the first inner marker it met, so this walks the
    markers and tracks depth instead.

    A start marker with no matching end marker leaves the text untouched
    from that point on, which is the conservative direction: an unbalanced
    marker should not silently swallow the rest of a document.
    """
    out: list[str] = []
    depth = 0
    region_start = 0
    cursor = 0

    for match in ALLOWLIST_MARKER.finditer(text):
        if match.group("kind") == "start":
            if depth == 0:
                region_start = match.start()
            depth += 1
            continue
        depth -= 1
        if depth == 0:
            out.append(text[cursor:region_start])
            cursor = match.end()
        elif depth < 0:
            # An end marker with no open region. Treat everything from the
            # cursor as ordinary text.
            depth = 0

    out.append(text[cursor:])
    return "".join(out)


def iter_scannable_files(repo: Path = REPO, exclude: Iterable[Path] = ()) -> Iterable[Path]:
    """Yield every file the content scans should look at."""
    excluded = set(exclude)
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SCAN_SKIP_DIRS for part in path.parts):
            continue
        if path in excluded:
            continue
        if path.suffix in SCAN_SUFFIXES or path.name in SCAN_NAMES:
            yield path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def rel(path: Path, repo: Path = REPO) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def scan_text(
    text: str,
    rules: Sequence[tuple[str, re.Pattern[str]]],
    path: str,
) -> list[Finding]:
    """Apply a set of labelled regular expressions to one blob of text."""
    findings: list[Finding] = []
    for label, pattern in rules:
        for match in pattern.finditer(text):
            line_number = text.count("\n", 0, match.start()) + 1
            excerpt = match.group(0).strip()
            if len(excerpt) > 60:
                excerpt = excerpt[:57] + "..."
            findings.append(Finding(label, path, line_number, excerpt))
    return findings


# ---------------------------------------------------------------------------
# Individual detectors (each is a pure function so it can be unit tested)
# ---------------------------------------------------------------------------


def detect_forbidden_commercial(text: str, path: str = "text") -> list[Finding]:
    """Find licensing language, commercial figures, and competitor vendors."""
    return scan_text(strip_allowlist_regions(text), FORBIDDEN_COMMERCIAL, path)


def detect_pii(text: str, path: str = "text") -> list[Finding]:
    """Find email addresses, phone numbers, and social security numbers."""
    return scan_text(text, PII_PATTERNS, path)


def detect_secrets(text: str, path: str = "text") -> list[Finding]:
    """Find credential-shaped strings."""
    return scan_text(text, SECRET_PATTERNS, path)


def detect_style(text: str, path: str = "text") -> list[Finding]:
    """Find banned marketing vocabulary and em dashes."""
    findings: list[Finding] = []
    stripped = strip_allowlist_regions(text)
    for word in BANNED_VOCABULARY:
        for match in re.finditer(rf"\b{re.escape(word)}\b", stripped, re.IGNORECASE):
            line_number = stripped.count("\n", 0, match.start()) + 1
            findings.append(Finding("banned vocabulary", path, line_number, match.group(0)))
    for match in re.finditer(re.escape(EM_DASH), stripped):
        line_number = stripped.count("\n", 0, match.start()) + 1
        findings.append(Finding("em dash", path, line_number, "em dash"))
    return findings


def detect_mermaid_issues(text: str, path: str = "text") -> list[Finding]:
    """Check that every mermaid block is well formed.

    The checks are structural rather than semantic: a valid diagram that a
    renderer rejects is still a broken diagram. We verify the header
    keyword, that quotes and brackets balance, that subgraphs are closed,
    and that the block is not empty.
    """
    findings: list[Finding] = []
    blocks = list(re.finditer(r"```mermaid\n(.*?)```", text, re.DOTALL))
    if not blocks:
        return findings

    for index, block in enumerate(blocks, start=1):
        body = block.group(1)
        start_line = text.count("\n", 0, block.start()) + 1

        if not body.strip():
            findings.append(Finding("empty mermaid block", path, start_line, f"block {index}"))
            continue

        if "\t" in body:
            findings.append(Finding("tab in mermaid block", path, start_line, f"block {index}"))

        header = body.strip().splitlines()[0].strip()
        first_word = header.split()[0] if header.split() else ""
        if first_word not in MERMAID_HEADERS:
            findings.append(
                Finding("bad mermaid header", path, start_line, f"block {index}: {header[:40]}")
            )

        if body.count('"') % 2 != 0:
            findings.append(Finding("unbalanced quotes in mermaid", path, start_line, f"block {index}"))

        for opener, closer, label in (("[", "]", "brackets"), ("(", ")", "parentheses")):
            if body.count(opener) != body.count(closer):
                findings.append(
                    Finding(f"unbalanced {label} in mermaid", path, start_line, f"block {index}")
                )

        subgraph_count = len(re.findall(r"^\s*subgraph\b", body, re.MULTILINE))
        end_count = len(re.findall(r"^\s*end\s*$", body, re.MULTILINE))
        if subgraph_count != end_count:
            findings.append(
                Finding(
                    "unclosed subgraph in mermaid",
                    path,
                    start_line,
                    f"block {index}: {subgraph_count} subgraph, {end_count} end",
                )
            )

        if not re.search(r"(-\.|-->|---|==>|-)", body):
            findings.append(Finding("mermaid block has no edges", path, start_line, f"block {index}"))

    return findings


def missing_readme_sections(text: str, required: Sequence[str] = REQUIRED_README_SECTIONS) -> list[str]:
    """Return the required README headings that are absent."""
    present = {
        match.group(1).strip().lower()
        for match in re.finditer(r"^#{1,6}\s+(.*)$", text, re.MULTILINE)
    }
    return [section for section in required if section.lower() not in present]


def parse_roi_table(text: str) -> dict[float, dict[str, float]]:
    """Parse the published deflection table out of the README.

    The table is the contract between the prose and ``src/roi.py``. If the
    prose drifts from the code, this parse plus G7 catches it.
    """
    rows: dict[float, dict[str, float]] = {}
    pattern = re.compile(
        r"^\|\s*(?P<rate>\d{1,3})%\s*\|\s*(?P<deflected>[\d,]+)\s*\|"
        r"\s*(?P<to_staff>[\d,]+)\s*\|\s*(?P<consolidation>[\d,]+)\s*\|"
        r"\s*(?P<handle>[\d,]+)\s*\|\s*(?P<fte>[\d.]+)\s*\|\s*$",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        rate = int(match.group("rate")) / 100.0
        rows[rate] = {
            "calls_deflected": int(match.group("deflected").replace(",", "")),
            "calls_to_staff": int(match.group("to_staff").replace(",", "")),
            "consolidation_hours": float(match.group("consolidation").replace(",", "")),
            "handle_time_hours": float(match.group("handle").replace(",", "")),
            "total_fte": float(match.group("fte")),
        }
    return rows


# ---------------------------------------------------------------------------
# Gate results
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str
    traceback: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("PASS", "SKIP")


class _C:
    def __init__(self) -> None:
        if sys.stdout.isatty() and os.environ.get("NO_COLOR") is None:
            self.R = "\033[31m"
            self.G = "\033[32m"
            self.Y = "\033[33m"
            self.B = "\033[1m"
            self.D = "\033[2m"
            self.X = "\033[0m"
        else:
            self.R = self.G = self.Y = self.B = self.D = self.X = ""

    def short(self, status: str) -> str:
        if status == "PASS":
            return f"{self.G}PASS{self.X}"
        if status == "SKIP":
            return f"{self.Y}SKIP{self.X}"
        return f"{self.R}FAIL{self.X}"


C = _C()


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def gate_required_files() -> GateResult:
    """G1: every required file is present."""
    missing = [name for name in REQUIRED_FILES if not (REPO / name).exists()]
    if missing:
        return GateResult("G1 required files", "FAIL", "missing: " + ", ".join(missing))
    return GateResult("G1 required files", "PASS", f"{len(REQUIRED_FILES)} files present")


def gate_no_commercial_terms() -> GateResult:
    """G2: no licensing language, commercial figures, or competitor vendors."""
    findings: list[Finding] = []
    for path in iter_scannable_files(exclude=STYLE_SCAN_EXCLUSIONS):
        findings.extend(detect_forbidden_commercial(read_text(path), rel(path)))
    if findings:
        return GateResult(
            "G2 commercial terms",
            "FAIL",
            f"{len(findings)} hit(s): " + "; ".join(str(f) for f in findings[:4]),
        )
    return GateResult("G2 commercial terms", "PASS", "no commercial terms or competitor vendors")


def gate_no_personal_data() -> GateResult:
    """G3: no email addresses, phone numbers, or social security numbers."""
    findings: list[Finding] = []
    for path in iter_scannable_files():
        findings.extend(detect_pii(read_text(path), rel(path)))
    if findings:
        return GateResult(
            "G3 personal data",
            "FAIL",
            f"{len(findings)} hit(s): " + "; ".join(str(f) for f in findings[:4]),
        )
    return GateResult("G3 personal data", "PASS", "no personal identifiers")


def gate_readme_sections() -> GateResult:
    """G4: the README carries every required section."""
    readme = REPO / "README.md"
    if not readme.exists():
        return GateResult("G4 readme sections", "FAIL", "README.md missing")
    missing = missing_readme_sections(read_text(readme))
    if missing:
        return GateResult(
            "G4 readme sections",
            "FAIL",
            f"missing section(s): {', '.join(missing)}",
        )
    return GateResult(
        "G4 readme sections",
        "PASS",
        f"all {len(REQUIRED_README_SECTIONS)} sections present",
    )


def gate_mermaid() -> GateResult:
    """G5: mermaid blocks are structurally well formed."""
    findings: list[Finding] = []
    blocks_seen = 0
    for path in iter_scannable_files():
        if path.suffix != ".md":
            continue
        text = read_text(path)
        blocks_seen += len(re.findall(r"```mermaid\n", text))
        findings.extend(detect_mermaid_issues(text, rel(path)))
    if findings:
        return GateResult(
            "G5 mermaid",
            "FAIL",
            f"{len(findings)} issue(s): " + "; ".join(str(f) for f in findings[:4]),
        )
    if blocks_seen == 0:
        return GateResult("G5 mermaid", "FAIL", "no mermaid diagram found")
    return GateResult("G5 mermaid", "PASS", f"{blocks_seen} diagram(s) well formed")


def gate_no_secrets() -> GateResult:
    """G6: no credential material anywhere in the repository."""
    findings: list[Finding] = []
    for path in iter_scannable_files():
        findings.extend(detect_secrets(read_text(path), rel(path)))
    if findings:
        return GateResult(
            "G6 secrets",
            "FAIL",
            f"{len(findings)} hit(s): " + "; ".join(str(f) for f in findings[:4]),
        )
    return GateResult("G6 secrets", "PASS", "no credential material")


def gate_roi_consistency() -> GateResult:
    """G7: the published deflection table matches src/roi.py."""
    readme = REPO / "README.md"
    if not readme.exists():
        return GateResult("G7 roi consistency", "FAIL", "README.md missing")
    published = parse_roi_table(read_text(readme))
    if not published:
        return GateResult("G7 roi consistency", "FAIL", "no deflection table found in README.md")

    sys.path.insert(0, str(REPO))
    try:
        from src.roi import scenario  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        return GateResult("G7 roi consistency", "FAIL", f"could not import src.roi: {exc}")

    drift: list[str] = []
    for rate, claimed in sorted(published.items()):
        computed = scenario(deflection_rate=rate)
        checks = {
            "calls_deflected": (float(claimed["calls_deflected"]), float(computed.calls_deflected)),
            "calls_to_staff": (float(claimed["calls_to_staff"]), float(computed.calls_to_staff)),
            "consolidation_hours": (
                claimed["consolidation_hours"],
                round(computed.consolidation_hours),
            ),
            "handle_time_hours": (
                claimed["handle_time_hours"],
                round(computed.handle_time_hours),
            ),
            "total_fte": (claimed["total_fte"], round(computed.total_fte, 1)),
        }
        for field, (claimed_value, computed_value) in checks.items():
            if abs(claimed_value - computed_value) > 0.51:
                drift.append(f"{rate:.0%} {field}: README={claimed_value} code={computed_value}")

    if drift:
        return GateResult("G7 roi consistency", "FAIL", "; ".join(drift[:6]))
    return GateResult(
        "G7 roi consistency",
        "PASS",
        f"{len(published)} scenario row(s) match src/roi.py",
    )


def gate_prose_style() -> GateResult:
    """G8: no banned marketing vocabulary and no em dashes."""
    findings: list[Finding] = []
    for path in iter_scannable_files(exclude=STYLE_SCAN_EXCLUSIONS):
        if path.suffix not in {".md", ".py"}:
            continue
        findings.extend(detect_style(read_text(path), rel(path)))
    if findings:
        return GateResult(
            "G8 prose style",
            "FAIL",
            f"{len(findings)} hit(s): " + "; ".join(str(f) for f in findings[:4]),
        )
    return GateResult("G8 prose style", "PASS", "house style holds")


def gate_no_traceback() -> GateResult:
    """G9: no earlier check produced a Python traceback."""
    return GateResult("G9 no traceback", "PASS", "all checks clean")


CHECKS: tuple[tuple[str, Callable[[], GateResult]], ...] = (
    ("G1 required files", gate_required_files),
    ("G2 commercial terms", gate_no_commercial_terms),
    ("G3 personal data", gate_no_personal_data),
    ("G4 readme sections", gate_readme_sections),
    ("G5 mermaid", gate_mermaid),
    ("G6 secrets", gate_no_secrets),
    ("G7 roi consistency", gate_roi_consistency),
    ("G8 prose style", gate_prose_style),
    ("G9 no traceback", gate_no_traceback),
)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_checks(only: str | None = None) -> list[GateResult]:
    results: list[GateResult] = []
    for name, check in CHECKS:
        if only and not name.startswith(only):
            continue
        try:
            results.append(check())
        except Exception as exc:  # pragma: no cover - defensive
            results.append(
                GateResult(
                    name,
                    "FAIL",
                    f"check raised: {exc}",
                    traceback.format_exc(limit=6),
                )
            )
    return results


def print_table(results: Sequence[GateResult]) -> None:
    if not results:
        return
    width = max(len(r.name) for r in results)
    print(f"{C.B}Charlotte CRM case study quality gate{C.X} {datetime.now().isoformat(timespec='seconds')}")
    print()
    for result in results:
        status = C.short(result.status)
        detail = result.detail if len(result.detail) <= 150 else result.detail[:147] + "..."
        print(f"  {result.name.ljust(width)}  {status}  {C.D}{detail}{C.X}")
        if result.traceback:
            print(result.traceback)
    print()


def summary_line(results: Sequence[GateResult]) -> str:
    counts = Counter(r.status for r in results)
    overall = "PASS" if all(r.ok for r in results) else "FAIL"
    return (
        f"gate overall={overall} "
        f"pass={counts.get('PASS', 0)} skip={counts.get('SKIP', 0)} fail={counts.get('FAIL', 0)} "
        f"@{datetime.now().isoformat(timespec='seconds')}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quality gate for the Charlotte CRM case study.")
    parser.add_argument("--quiet", action="store_true", help="print only the summary line")
    parser.add_argument("--only", help="run only the check whose name starts with this prefix")
    args = parser.parse_args(argv)

    if not REPO.exists():
        print(f"repository root not found: {REPO}", file=sys.stderr)
        return 2

    results = run_checks(args.only)
    if args.only and not results:
        print(f"no check matches {args.only!r}", file=sys.stderr)
        return 2

    if not args.quiet:
        print_table(results)
    summary = summary_line(results)
    print(summary)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
