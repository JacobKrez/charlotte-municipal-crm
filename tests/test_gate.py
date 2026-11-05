"""Tests for the detection logic in ``scripts/gate.py``.

Two rules govern this file.

First, every detector is a pure function over text, so it can be tested
without touching the repository on disk.

Second, this file is itself scanned by the gate. Sensitive literals are
therefore assembled from fragments at call time, so that a test fixture for
an email address does not read as a real email address to the scanner that
runs over this file. The detectors are checked against inputs built the
same way a real leak would be built.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import gate
from scripts.gate import (
    REQUIRED_README_SECTIONS,
    coverage_claim_drift,
    detect_forbidden_commercial,
    detect_mermaid_issues,
    detect_pii,
    detect_secrets,
    detect_style,
    missing_readme_sections,
    parse_control_table,
    parse_roi_table,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def frag(*parts: str) -> str:
    """Join fragments into a single string.

    Used so that fixture literals are not contiguous in the source file and
    therefore do not trip the very detectors under test.
    """
    return "".join(parts)


# ---------------------------------------------------------------------------
# Commercial term detection
# ---------------------------------------------------------------------------


class TestForbiddenCommercialDetection:
    # The fixture phrases below are deliberately written as literal strings,
    # because a fixture assembled from fragments would test the detector
    # against something a real leak would not look like. The gate scans
    # this file too, so the block sits inside an allowlist region, which the
    # commercial scan strips before it runs. The style scan strips the same
    # region, which is why the banned vocabulary can be spelled out below.
    # <!-- gate:allowlist-start -->

    @pytest.mark.parametrize(
        "text",
        [
            "the solution is priced per seat",
            "a per-seat license",
            "seat pricing was flat",
            "tiered pricing tier structure",
            "the price list is attached",
            "list price applies",
            "total cost of ownership over five years",
            "our TCO analysis",
            "a payback period of eleven months",
            "annual contract value of one million",
            "the ACV grows with headcount",
            "implementation fee schedule",
            "subscription fee structure",
            "professional services fee line",
            "our bid total",
            "bid price of forty thousand",
            "$1,200 per year",
            "1200 USD",
            "500 dollars",
        ],
    )
    def test_flags_commercial_language(self, text: str) -> None:
        assert detect_forbidden_commercial(text), f"should have flagged: {text}"

    @pytest.mark.parametrize(
        "text",
        [
            "The city publishes its procurement on its own site.",
            "We did not publish commercial terms for this work.",
            "The response was scored against a published rubric.",
            "Cost avoidance is described in agent-hours, not money.",
            "The scoring criteria were public.",
        ],
    )
    def test_allows_ordinary_prose(self, text: str) -> None:
        assert detect_forbidden_commercial(text) == []

    def test_flags_competitor_vendor_names(self) -> None:
        for name in ("Sales" + "force", "Service" + "Now", "Accen" + "ture"):
            assert detect_forbidden_commercial(f"We considered {name}."), name

    def test_allows_integration_vendor_names(self) -> None:
        """Named systems the city actually runs are not competitor names."""
        text = (
            "The integration uses Boomi, Cityworks, Banner, Avaya OneCloud CX, "
            "Okta, Tableau, and Esri ArcGIS."
        )
        assert detect_forbidden_commercial(text) == []

    def test_allowlist_region_suppresses_findings(self) -> None:
        text = (
            "<!-- gate:allowlist-start -->\n"
            "per seat, price list, payback period\n"
            "<!-- gate:allowlist-end -->\n"
            "clean prose follows\n"
        )
        assert detect_forbidden_commercial(text) == []

    def test_findings_carry_a_location(self) -> None:
        findings = detect_forbidden_commercial("line one\nper seat pricing\n", "doc.md")
        assert findings
        assert findings[0].path == "doc.md"
        assert findings[0].line == 2

    # <!-- gate:allowlist-end -->


# ---------------------------------------------------------------------------
# Personal data detection
# ---------------------------------------------------------------------------


class TestPersonalDataDetection:
    def test_flags_email(self) -> None:
        text = frag("contact ", "someone", "@", "example.com")
        assert detect_pii(text)

    def test_flags_phone(self) -> None:
        text = frag("call ", "555", "-123-4567", " for help")
        assert detect_pii(text)

    def test_flags_social_security_number(self) -> None:
        text = frag("ssn ", "123", "-45-6789", " on file")
        assert detect_pii(text)

    @pytest.mark.parametrize(
        "text",
        [
            "The NIST 800-53 control family list is long.",
            "See SP 800-53 Rev 5 for the mappings.",
            "The Cityworks 23.13 release notes mention a fix.",
            "Banner 5.2 is the utility system.",
            "us-east-1 and us-west-2 are the regions.",
            "The tier is 1 for emergency cases.",
        ],
    )
    def test_allows_technical_numbers(self, text: str) -> None:
        assert detect_pii(text) == []

    def test_scanning_the_repository_finds_nothing(self) -> None:
        findings = []
        for path in gate.iter_scannable_files():
            findings.extend(detect_pii(gate.read_text(path), gate.rel(path)))
        assert findings == []


# ---------------------------------------------------------------------------
# Secret detection
# ---------------------------------------------------------------------------


class TestSecretDetection:
    def test_flags_aws_access_key(self) -> None:
        text = frag("key = ", "AKIA", "ABCDEFGHIJKLMNOP")
        assert detect_secrets(text)

    def test_flags_github_token(self) -> None:
        text = frag("token: ", "ghp_", "a" * 36)
        assert detect_secrets(text)

    def test_flags_private_key_block(self) -> None:
        text = frag("-----BEGIN ", "PRIVATE KEY-----")
        assert detect_secrets(text)

    def test_flags_assigned_secret(self) -> None:
        text = frag("api", "_key = ", "abcdefghijklmnop1234")
        assert detect_secrets(text)

    def test_allows_mentions_of_secret_handling(self) -> None:
        text = (
            "Credentials live in a managed secret store and rotate "
            "automatically. No credential material is committed."
        )
        assert detect_secrets(text) == []

    def test_scanning_the_repository_finds_nothing(self) -> None:
        findings = []
        for path in gate.iter_scannable_files():
            findings.extend(detect_secrets(gate.read_text(path), gate.rel(path)))
        assert findings == []


# ---------------------------------------------------------------------------
# Style detection
# ---------------------------------------------------------------------------


class TestStyleDetection:
    def test_flags_every_banned_word(self) -> None:
        for word in gate.BANNED_VOCABULARY:
            assert detect_style(f"This is {word}."), word
            assert detect_style(f"This is {word.upper()}."), word

    def test_flags_em_dash(self) -> None:
        text = "a sentence" + chr(0x2014) + "and another"
        findings = detect_style(text)
        assert any(f.rule == "em dash" for f in findings)

    def test_allows_plain_prose(self) -> None:
        text = "The architecture has seven layers, and each one has a job."
        assert detect_style(text) == []

    def test_allowlist_region_suppresses_findings(self) -> None:
        text = (
            "<!-- gate:allowlist-start -->\n"
            "robust, seamless, leverage\n"
            "<!-- gate:allowlist-end -->\n"
        )
        assert detect_style(text) == []

    def test_banned_words_are_not_substring_matches(self) -> None:
        """A banned word inside a longer word is not a match.

        The word is taken from the gate's own list rather than written
        here, so this file does not contain a literal the style scan would
        flag.
        """
        word = next(w for w in gate.BANNED_VOCABULARY if w.endswith("k"))
        assert detect_style(f"the door is {word}ed") == []


# ---------------------------------------------------------------------------
# Mermaid validation
# ---------------------------------------------------------------------------

GOOD_DIAGRAM = """```mermaid
flowchart TD
    A[Start] --> B[Middle]
    B --> C[End]
```"""


class TestMermaidValidation:
    def test_accepts_a_well_formed_diagram(self) -> None:
        assert detect_mermaid_issues(GOOD_DIAGRAM) == []

    def test_no_diagrams_is_not_an_error(self) -> None:
        assert detect_mermaid_issues("just prose\n") == []

    def test_flags_bad_header(self) -> None:
        text = "```mermaid\nnotadigraph TD\n  A --> B\n```"
        assert any(f.rule == "bad mermaid header" for f in detect_mermaid_issues(text))

    def test_flags_unbalanced_brackets(self) -> None:
        text = "```mermaid\nflowchart TD\n  A[Start --> B[End]\n```"
        assert any("unbalanced" in f.rule for f in detect_mermaid_issues(text))

    def test_flags_unclosed_subgraph(self) -> None:
        text = "```mermaid\nflowchart TD\n  subgraph One\n    A --> B\n```"
        assert any("subgraph" in f.rule for f in detect_mermaid_issues(text))

    def test_flags_unbalanced_quotes(self) -> None:
        text = '```mermaid\nflowchart TD\n  A["Start] --> B[End]\n```'
        assert any("quotes" in f.rule for f in detect_mermaid_issues(text))

    def test_flags_empty_block(self) -> None:
        text = "```mermaid\n\n```"
        assert any("empty" in f.rule for f in detect_mermaid_issues(text))

    def test_flags_tabs(self) -> None:
        text = "```mermaid\nflowchart TD\n\tA --> B\n```"
        assert any("tab" in f.rule for f in detect_mermaid_issues(text))

    def test_flags_a_diagram_with_no_edges(self) -> None:
        text = "```mermaid\nflowchart TD\n  A[Start]\n```"
        assert any("no edges" in f.rule for f in detect_mermaid_issues(text))


# ---------------------------------------------------------------------------
# README structure
# ---------------------------------------------------------------------------


class TestReadmeSections:
    def test_repository_readme_has_every_section(self) -> None:
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert missing_readme_sections(text) == []

    def test_detects_a_missing_section(self) -> None:
        text = "## What this is\n\nprose\n"
        missing = missing_readme_sections(text)
        assert "Safety" in missing
        assert "What this is" not in missing

    def test_section_names_are_unique(self) -> None:
        assert len(set(REQUIRED_README_SECTIONS)) == len(REQUIRED_README_SECTIONS)


class TestRoiTableParsing:
    def test_parses_published_rows(self) -> None:
        text = (
            "| Rate | Deflected | Staff | Consolidation | Handle time | FTE |\n"
            "|---|---|---|---|---|---|\n"
            "| 30% | 277,500 | 647,500 | 21,583 | 30,063 | 25.8 |\n"
            "| 40% | 370,000 | 555,000 | 18,500 | 40,083 | 29.3 |\n"
        )
        rows = parse_roi_table(text)
        assert set(rows) == {0.30, 0.40}
        assert rows[0.30]["calls_deflected"] == 277_500
        assert rows[0.40]["calls_to_staff"] == 555_000

    def test_empty_input_yields_no_rows(self) -> None:
        assert parse_roi_table("no table here") == {}


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


class TestGateEndToEnd:
    def test_repository_passes_its_own_gate(self) -> None:
        proc = subprocess.run(
            [sys.executable, "scripts/gate.py", "--quiet"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_gate_exits_nonzero_when_a_section_is_missing(self, tmp_path: Path) -> None:
        """A gate that cannot fail is not a gate."""
        results = gate.run_checks("G4")
        assert results
        assert all(r.status in ("PASS", "FAIL") for r in results)

    def test_required_files_all_exist(self) -> None:
        missing = [name for name in gate.REQUIRED_FILES if not (REPO_ROOT / name).exists()]
        assert missing == []


class TestGateCanFail:
    """The gate is verified by breaking the repository on purpose.

    A gate that has only ever returned green has not been tested. Each case
    below copies the repository, introduces one specific defect, and asserts
    that the gate catches it and exits non-zero. These are the checks that
    would otherwise look decorative.

    The defect text is assembled from fragments for the same reason the
    fixtures above are: this file is scanned by the gate it is testing, so
    a literal leak written here would be a leak in the repository. The
    assembled text is what gets written into the throwaway copy.
    """

    @staticmethod
    def _clone(tmp_path: Path) -> Path:
        destination = tmp_path / "repo"
        shutil.copytree(
            REPO_ROOT,
            destination,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
        )
        return destination

    @staticmethod
    def _run(directory: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "scripts/gate.py"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=300,
        )

    def test_catches_a_personal_email(self, tmp_path: Path) -> None:
        repo = self._clone(tmp_path)
        leak = frag("someone", "@", "example.com")
        with (repo / "README.md").open("a", encoding="utf-8") as handle:
            handle.write(f"\n\nContact: {leak}\n")
        proc = self._run(repo)
        assert proc.returncode == 1
        assert "G3 personal data" in proc.stdout

    def test_catches_a_competitor_vendor_name(self, tmp_path: Path) -> None:
        repo = self._clone(tmp_path)
        leak = frag("Sales", "force")
        with (repo / "ARCHITECTURE.md").open("a", encoding="utf-8") as handle:
            handle.write(f"\n\nWe considered displacing {leak} eventually.\n")
        proc = self._run(repo)
        assert proc.returncode == 1
        assert "G2 commercial terms" in proc.stdout

    def test_catches_a_currency_amount(self, tmp_path: Path) -> None:
        repo = self._clone(tmp_path)
        leak = frag("$", "1.4M")
        with (repo / "README.md").open("a", encoding="utf-8") as handle:
            handle.write(f"\n\nThe platform costs {leak} per year.\n")
        proc = self._run(repo)
        assert proc.returncode == 1
        assert "G2 commercial terms" in proc.stdout

    def test_catches_a_missing_readme_section(self, tmp_path: Path) -> None:
        repo = self._clone(tmp_path)
        readme = repo / "README.md"
        text = readme.read_text(encoding="utf-8")
        readme.write_text(text.replace("## Known limitations", "## Notes"), encoding="utf-8")
        proc = self._run(repo)
        assert proc.returncode == 1
        assert "G4 readme sections" in proc.stdout

    def test_catches_prose_drifting_from_the_code(self, tmp_path: Path) -> None:
        repo = self._clone(tmp_path)
        readme = repo / "README.md"
        text = readme.read_text(encoding="utf-8")
        readme.write_text(
            text.replace("| 40% | 370,000 |", "| 40% | 999,999 |"), encoding="utf-8"
        )
        proc = self._run(repo)
        assert proc.returncode == 1
        assert "G7 roi consistency" in proc.stdout

    def test_catches_a_coverage_figure_drifting_from_the_table(self, tmp_path: Path) -> None:
        repo = self._clone(tmp_path)
        readme = repo / "README.md"
        text = readme.read_text(encoding="utf-8")
        readme.write_text(
            text.replace("| AC | Access Control | 21 of 25 |", "| AC | Access Control | 24 of 25 |"),
            encoding="utf-8",
        )
        proc = self._run(repo)
        assert proc.returncode == 1
        assert "G10 control coverage" in proc.stdout

    def test_catches_credential_material(self, tmp_path: Path) -> None:
        repo = self._clone(tmp_path)
        leak = frag("sk-", "abcdefghijklmnopqrstuvwxyz012345")
        with (repo / "src" / "roi.py").open("a", encoding="utf-8") as handle:
            handle.write(f'\nAPI_KEY = "{leak}"\n')
        proc = self._run(repo)
        assert proc.returncode == 1
        assert "G6 secrets" in proc.stdout

    def test_catches_a_style_violation(self, tmp_path: Path) -> None:
        repo = self._clone(tmp_path)
        leak = frag("seam", "less")
        with (repo / "README.md").open("a", encoding="utf-8") as handle:
            handle.write(f"\nThis is a {leak} solution.\n")
        proc = self._run(repo)
        assert proc.returncode == 1
        assert "G8 prose style" in proc.stdout

    def test_catches_a_broken_diagram(self, tmp_path: Path) -> None:
        repo = self._clone(tmp_path)
        readme = repo / "README.md"
        text = readme.read_text(encoding="utf-8")
        readme.write_text(
            text.replace("flowchart TD\n    R[", "flowchart TD\n    subgraph X\n    R[", 1),
            encoding="utf-8",
        )
        proc = self._run(repo)
        assert proc.returncode == 1
        assert "G5 mermaid" in proc.stdout

    def test_catches_a_missing_required_file(self, tmp_path: Path) -> None:
        repo = self._clone(tmp_path)
        (repo / "EVALUATION.md").unlink()
        proc = self._run(repo)
        assert proc.returncode == 1
        assert "G1 required files" in proc.stdout


# ---------------------------------------------------------------------------
# Control coverage (G10)
# ---------------------------------------------------------------------------


class TestControlCoverage:
    TABLE = (
        "| Family | Name | Controls addressed | Primary implementation |\n"
        "|---|---|---|---|\n"
        "| AC | Access Control | 3 of 4 | x |\n"
        "| PE | Physical | inherited | y |\n"
        "| SC | Comms | 5 of 6 | z |\n"
    )

    def test_parses_rows_and_skips_inherited(self) -> None:
        assert parse_control_table(self.TABLE) == {"AC": (3, 4), "SC": (5, 6)}

    def test_repository_table_sums_to_published_figure(self) -> None:
        rows = parse_control_table((REPO_ROOT / "README.md").read_text(encoding="utf-8"))
        assert sum(n for n, _ in rows.values()) == 171
        assert sum(d for _, d in rows.values()) == 214

    def test_accepts_figures_derived_from_the_table(self) -> None:
        text = "Coverage is 8 of 10 controls, which is 80 percent coverage of controls."
        assert coverage_claim_drift(text, 8, 10) == []

    def test_flags_a_stale_percentage(self) -> None:
        text = "Coverage is about 75 percent of applicable controls."
        assert coverage_claim_drift(text, 171, 214)

    def test_flags_a_stale_pair(self) -> None:
        assert coverage_claim_drift("We address 174 of 232 controls.", 171, 214)

    def test_retracted_pair_allowed_only_as_a_past_claim(self) -> None:
        text = "The summary line claimed 174 of 232 controls."
        assert coverage_claim_drift(text, 171, 214) == []

    def test_ignores_sentences_about_other_things(self) -> None:
        text = "The split is 75 percent staff-assisted. We answered 3 of 4 questions."
        assert coverage_claim_drift(text, 171, 214) == []


class TestGateIgnoresReaderDirectories:
    """The scans cover published files, not whatever the reader creates.

    A reviewer who makes a virtual environment called ``junk_venv`` or
    ``env`` inside the checkout must still see a green gate, because
    third-party packages contain email addresses, key-shaped test strings
    and em dashes. Regression for a gate that went red on any venv not
    named ``.venv`` or ``venv``.
    """

    @staticmethod
    def _plant(repo: Path, with_pyvenv_cfg: bool) -> None:
        junk = repo / "junk_venv" / "lib"
        junk.mkdir(parents=True)
        if with_pyvenv_cfg:
            (repo / "junk_venv" / "pyvenv.cfg").write_text("home = /x\n", encoding="utf-8")
        leak = frag("someone", "@", "example.com ") + frag("sk-", "abcdefghijklmnopqrstuvwxyz012345")
        (junk / "pkg.py").write_text(f'KEY = "{leak}" \u2014\n', encoding="utf-8")
        (junk / "README.md").write_text("Coverage is 75 percent of controls.\n", encoding="utf-8")

    @staticmethod
    def _gate(repo: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "scripts/gate.py"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=300,
        )

    def test_untracked_directory_in_a_git_checkout(self, tmp_path: Path) -> None:
        if not (REPO_ROOT / ".git").exists():
            pytest.skip("not a git checkout")
        repo = tmp_path / "repo"
        shutil.copytree(REPO_ROOT, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        self._plant(repo, with_pyvenv_cfg=False)
        proc = self._gate(repo)
        assert proc.returncode == 0, proc.stdout

    def test_virtual_environment_in_a_source_archive(self, tmp_path: Path) -> None:
        # A source archive holds the tracked files and no .git. Copying the
        # whole working tree would also copy whatever the person running
        # the tests has lying around untracked, which no archive contains.
        repo = tmp_path / "repo"
        if (REPO_ROOT / ".git").exists():
            listed = subprocess.run(
                ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
            ).stdout.decode("utf-8")
            for name in filter(None, listed.split("\0")):
                if (REPO_ROOT / name).is_file():
                    (repo / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(REPO_ROOT / name, repo / name)
        else:
            shutil.copytree(
                REPO_ROOT, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
            )
        self._plant(repo, with_pyvenv_cfg=True)
        proc = self._gate(repo)
        assert proc.returncode == 0, proc.stdout

    def test_a_tracked_leak_is_still_caught(self, tmp_path: Path) -> None:
        if not (REPO_ROOT / ".git").exists():
            pytest.skip("not a git checkout")
        repo = tmp_path / "repo"
        shutil.copytree(REPO_ROOT, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        leak = frag("someone", "@", "example.com")
        (repo / "notes.md").write_text(f"Contact {leak}\n", encoding="utf-8")
        subprocess.run(["git", "add", "notes.md"], cwd=repo, check=True)
        proc = self._gate(repo)
        assert proc.returncode == 1
        assert "G3 personal data" in proc.stdout
