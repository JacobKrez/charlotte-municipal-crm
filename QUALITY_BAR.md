# Quality bar

This file is the source of truth for what "done" means in this repository.
`scripts/gate.py` is the executable form of it. Every threshold below states
the number, the reason we picked that number rather than a different one, and
what we would do if we missed it.

A reviewer will ask "why this number and not the next one up" several times.
This document is the answer, written down. When a threshold moves, this file
changes first and the gate follows.

---

## How to run it

```
python3 scripts/gate.py             # full gate, tabulated
python3 scripts/gate.py --quiet     # one-line summary
python3 scripts/gate.py --only G4   # run a single check
```

Exit code 0 means green. Exit code 1 means at least one check failed. The
gate is the final step in CI, so a red gate blocks the build.

---

## The gates

### G1: required files present

- **Threshold:** every file in `REQUIRED_FILES` exists.
- **Why:** the repository has one job, which is to be read and checked. A
  reader who opens the README and finds the architecture document missing has
  been sent somewhere with no map, and no amount of prose in the README
  compensates. The list includes the test suite and the CI workflow because a
  gate that nobody runs is a gate that does not exist.
- **What happens below it:** the build fails. There is no partial credit.

### G2: no commercial terms

- **Threshold:** no occurrence of any pattern in `FORBIDDEN_COMMERCIAL` in
  any published file.
- **Why this threshold exists at all:** this repository documents a response
  to a live municipal procurement. Commercial terms from that response are
  competitive information that does not belong in a public repository, and
  publishing them would be a mistake that cannot be walked back once the
  repository is indexed. The check is automated rather than trusted to
  reviewer discipline, because the cost of a leak is borne by someone else.
- **What the patterns cover:** currency amounts, seat-based licensing
  language, cost and payback framing, contract-value framing, fee
  schedules, commercial figures, competitor vendor names, and competitor
  product names.
- **What is deliberately allowed:** the names of the systems the city
  actually runs, because the integration strategy is the engineering. A
  proposal that refused to name the platforms it integrates with would be
  less informative, not more careful.
- **The exception, and it is one file:** `scripts/gate.py` is skipped by this
  check, because the file necessarily contains the literal strings it looks
  for. No other file in the repository is exempt, including the tests. The
  test fixtures assemble their sensitive literals at runtime for exactly
  this reason.
- **What happens below it:** the build fails with the file, the line, and
  the matched text.

<!-- gate:allowlist-start -->
The literal patterns this check looks for, for a reader who wants to audit
the check itself: per-seat, seat price, pricing tier, price list, list
price, total cost of ownership, payback period, cost estimate, cost
framework, cost model, annual contract value, fee schedule, implementation
fee, subscription fee, professional services fee, seat fee, bid total, bid
price, bid amount, bid value, our bid, price point, and the competitor
vendor and product names.
<!-- gate:allowlist-end -->

### G3: no personal data

- **Threshold:** no email address, no phone number, and no social security
  number in any file in the repository.
- **Scope, stated precisely:** this gate scans the working tree, not the
  repository history. A commit author's name and email live in the git
  metadata, which is inherent to publishing under your own identity and is
  visible on every public repository. What the gate guarantees is that no
  *file* in the tree carries a personal identifier, which is where a leak
  from source material would actually land.
- **Why:** this repository is public and permanent. The subjects of the
  city's 311 calls are residents, and the people who appear in a municipal
  CRM's documentation are city staff and vendor contacts. None of them
  consented to appearing in a public repository, and none of them need to
  for this material to be useful. A government system that leaks resident
  data is a government system that loses trust; a public case study that
  leaks staff contact details earns the same criticism for a fraction of the
  consequence.
- **Why the patterns are shape-based rather than name-based:** a name list
  would be incomplete the moment a new person appears. Matching the shapes
  of the identifiers catches what matters without needing to know who.
- **What happens below it:** the build fails with the file, the line, and
  the matched text.

### G4: README sections present

- **Threshold:** the README contains every heading in
  `REQUIRED_README_SECTIONS`.
- **Why these particular sections:** they are the questions a reader
  arrives with, in the order they arrive with them. What is this, what did
  you leave out and why, how do I run it, how is it built, how does the
  interesting path work, what does the security posture look like, what
  happens when it breaks, what should I distrust, what comes next, where do
  I look, and is it safe to run.
- **Why "what we cut, and why" is mandatory rather than optional:** a case
  study that omits what was removed reads as though nothing was removed, and
  a reviewer who later discovers the omission has to re-evaluate everything
  else in the document. Naming the cuts is what makes the remaining claims
  checkable.
- **Why "known limitations" is mandatory:** a proposal that lists only its
  strengths is not usable. This section is where most of the reviewable
  content is, and a reviewer who skips it is reading the wrong document.
- **What happens below it:** the build fails and names the missing
  sections.

### G5: mermaid diagrams well formed

- **Threshold:** every mermaid block has a recognized diagram header, balanced
  quotes, balanced brackets and parentheses, matched `subgraph` and `end`
  counts, no tabs, and at least one edge.
- **Why:** a diagram that fails to render is worse than no diagram, because
  the reader assumes they are missing something rather than that the
  document is broken. GitHub renders mermaid in the README, so a broken
  block is visible to everyone.
- **Why these checks are structural rather than semantic:** we are not
  building a mermaid parser. We are checking the failure modes we have
  actually seen: an unclosed subgraph, an unbalanced bracket inside a label,
  a stray tab, and an empty block left behind after an edit.
- **What happens below it:** the build fails with the block number and the
  problem.

### G6: no secrets

- **Threshold:** no credential-shaped string in any file in the repository.
- **Why:** the repository is public. A key in a public repository is
  compromised at the moment of the push, not at the moment of the leak, and
  the push history is forever. The check covers cloud access key
  identifiers, forge tokens, chat platform tokens, model provider keys, PEM
  private key blocks, and assigned secret values in configuration.
- **Why this scans every file and not just the source:** secrets land in
  notebooks, fixtures, YAML, and JSON at least as often as they land in
  Python.
- **What happens below it:** the build fails with the file, the line, and
  the matched text.

### G7: the published numbers match the code

- **Threshold:** every row of the deflection table in the README equals what
  `src/roi.py` computes for the same inputs.
- **Why this check exists:** it is the only check in the repository that
  tests a *claim* rather than a *file*. A case study whose numbers are
  transcribed by hand drifts from the code that produced them, usually
  silently, and the drift is always in the flattering direction. This check
  makes the transcription impossible to leave stale.
- **Why the tolerance is 0.51 rather than exact equality:** the published
  table rounds hours to whole numbers and FTE to one decimal. The check
  compares the rounded published value against the rounded computed value,
  with a half-unit tolerance to absorb floating point representation. The
  underlying arithmetic is exact integer arithmetic on call counts.
- **Why this matters for a reviewer:** it means you can change an assumption
  in `src/roi.py`, re-run the gate, and find out immediately which published
  numbers are now wrong. The check is a two-way contract between prose and
  code, not a one-way assertion that the code is right.
- **What happens below it:** the build fails and names the row and the field
  that disagree.

### G8: house style

- **Threshold:** no em dashes and no banned marketing vocabulary in any
  Markdown or Python file, excluding `scripts/gate.py`.
- **Why we police our own prose:** the documents in this repository make
  claims about a public system that a resident might rely on. Marketing
  vocabulary is a symptom of a specific failure: it substitutes the
  feeling of importance for the information a reader needs. An em dash does
  the same thing structurally, by making a sentence look finished before it
  says anything.
- **Why the ban list is short:** a long list of banned words is a list of
  words we have decided to use. Twelve words that reliably make technical
  writing worse is enough, and the list is enforced in code rather than
  remembered.
- **Why the exception is one file:** `scripts/gate.py` contains the banned
  list by definition, exactly as it contains the commercial patterns.
- **What happens below it:** the build fails with the file, the line, and
  the matched text.

<!-- gate:allowlist-start -->
The banned vocabulary, for a reader auditing the check: revolutionary,
game-changing, cutting-edge, seamless, world-class, leverage, synergy,
empower, unlock, delight, robust, best-in-class.
<!-- gate:allowlist-end -->

### G9: no traceback

- **Threshold:** no check raised an unhandled exception.
- **Why:** a check that crashes is not a passing check, and a gate that
  swallows exceptions and reports green is worse than no gate, because it
  manufactures false confidence. Each check is wrapped, and a crash is
  reported as a failure with its traceback.
- **What happens below it:** the build fails.

---

## The thresholds this repository holds itself to

Beyond the gate, these are the standards we hold the material to. They are
not enforced by code because they are judgments, not properties of a file.

### On numbers

Every number in these documents is one of three things, and it says which:

| Kind | Meaning | Examples |
|---|---|---|
| City-confirmed | Disclosed by the city to us in the RFI process; a fact about the city | 925,000 calls a year; 694,000 service requests; 75 to 80 agents; 28 systems |
| Derived | Arithmetic on city-confirmed figures, reproducible from the code | Calls deflected; agent-hours recovered; FTE-equivalents |
| Assumption | Ours, not disclosed by the city, and labeled as such everywhere it is used | Handle time; minutes saved per call; productive hours per FTE; cost per call |

The requirement is not that every number is city-confirmed. It is that a
reader can tell which kind they are looking at without guessing. A document
that mixes a confirmed volume with an assumed rate in the same sentence
without marking the difference is the failure mode we are guarding against,
because it produces a conclusion with more precision than its inputs.

**The test we apply:** for every claim, can a reader reconstruct the number
from the stated inputs? If not, the number is either wrong or missing its
provenance, and we fix it before publishing.

### On the two figures that did not reconcile

Our working notes contained two arithmetic problems, and both are documented
in the README rather than quietly corrected:

1. **Two different denominators for the staff-handled call count.** The
   city's 75/25 split applies to resident-submitted intake (694,000
   requests), not to all inbound calls (925,000). Applying it to the wrong
   base changes the staff-handled count by about 173,000 calls a year. The
   published table uses one denominator, and it is named.
2. **A recovered-hours figure computed against the wrong base.** The
   working notes computed the workspace saving against the staff-handled
   calls *before* deflection, while the deflection table in the same
   document computed against the calls *after* it. At the 40 percent case
   those are 23,125 hours and 18,500 hours, and both appeared in the
   document without being reconciled.
3. **A control coverage summary that did not match its own table.** The
   summary claimed 174 of 232 controls; the family rows summed to 171 of
   214. The two cannot both come from the same rows, and the difference is
   consistent with the inherited physical-protection family having been
   counted in one and not the other.

We could have published either number in either case and nobody reading the
document would have known. That is precisely the reason we publish the
family table as the source of truth, decline to write a headline percentage
we cannot reproduce from the rows, and say here what happened.

### On claims and evidence

| Claim type | Standard |
|---|---|
| Architectural | May be stated as design. We designed it; we do not claim it is running. |
| Control mapping | May be stated as mapping. It identifies which element carries a control. It is not an attestation of operating effectiveness. |
| Certification | May be claimed only for something we actually hold. We hold none of our own today, and the README says so. |
| Performance | May be stated as a target with its measurement method. Never as a result, because we have measured nothing. |
| Market or competitive | Excluded from this repository, with the reason stated in the README. |
| Cost | Excluded from this repository. We express capacity in hours and FTE, and we take any per-call cost as an argument to the arithmetic rather than publishing a guess. |

### On open questions

Where the design depends on a decision we do not have, we name the decision
and name who has to make it. The equity metric definition is the clearest
example: we built the machinery, and the city has to decide what equitable
service means for Charlotte. A design that quietly assumed an answer to that
question would be easier to present and worse to build.

---

## What is verified and what is asserted

The most useful thing this document can do is separate those two things
without hedging.

### Verified

These are checked by the gate, on every commit, and a reviewer can re-run
them in a few seconds.

| Claim | How it is verified |
|---|---|
| The repository contains the required files | G1, file existence |
| No commercial terms or competitor names are published | G2, pattern scan over every published file |
| No personal identifiers are published | G3, shape-based pattern scan over every file |
| The README has every required section | G4, heading parse |
| The diagrams render | G5, structural validation of every mermaid block |
| No credential material is published | G6, pattern scan over every file |
| The published deflection numbers match the code | G7, recomputation from `src/roi.py` |
| The prose follows the house style | G8, pattern scan over every Markdown and Python file |
| The arithmetic behaves as documented | The pytest suite, covering boundary conditions, invalid inputs, and the properties that must hold regardless of inputs |
| The gate's own detectors work | The pytest suite, which tests each detector against inputs that must be flagged and inputs that must not, and which runs the gate against the repository end to end |

### Asserted, with the reasoning given

These are design decisions and judgments. We hold them to be defensible, not
to be mechanically checkable, and we have written down the reasoning so a
reviewer can disagree with the reasoning rather than with an unexplained
choice.

| Claim | Why we believe it |
|---|---|
| Seven layers with knowledge separated from inference | They have different lifecycles and different owners; merging them makes the review workflow inexpressible |
| Rule-based routing underneath AI routing | The fallback always exists, so confidence thresholds can be raised until accuracy clears the bar |
| Warm transfer with a structured payload | It is the change residents notice, and it is what makes the agent-time saving real rather than theoretical |
| Inference in the city's own account | It satisfies four stated requirements at once; the alternative was more expensive at this scale and added a patch surface |
| Single tenant per city | Makes the data boundary an account boundary, which is what a government security review accepts |
| Boomi for back-office integration | The city already runs it, so it owns the connections and the legacy middleware can consolidate onto it |
| Reporting through one set of conformed marts | The existing multi-hop chain exists because copies drifted; one mart set with suppression applied once removes the drift |
| Tamper-evident audit chains | The requirement is log integrity, not just log retention, and append-only logs do not deliver integrity |
| Guardrails at the platform layer | Content filtering, redaction, and injection defense need to apply to every path, including ones we did not write |
| Evaluation thresholds in the 90s rather than the high 90s | The cost of a miss is a delay and a transfer; tightening further shrinks the capacity benefit without reducing the resident-facing harm much |
| Emergency triage at 100 percent and no lower | It is a different kind of failure from a misrouted request, and no aggregate percentage substitutes for it |
| Reporting failures with the same prominence as passes | A committee that only sees passing numbers makes decisions on a filtered view of the system |

### Explicitly not claimed

* That any of this was deployed, or is running, or was tested against
  resident traffic.
* That any control is certified, audited, or operating effectively for a
  twelve-month window.
* That the deflection rate will be any particular number.
* That the system is cheaper, faster, or better than an alternative we did
  not build.
* That the equity view will show equitable service.

---

## Changing a threshold

1. Change the number in this file, with the new reasoning.
2. Change the matching constant in `scripts/gate.py`, if the threshold is
   enforced there.
3. Run `python3 scripts/gate.py` and confirm the new threshold is the one
   being enforced.
4. Run `python3 -m pytest -q` and confirm the suite still passes.
5. Commit both changes together.

Thresholds do not move to make a build pass. A build that fails on a
threshold we set is the gate working. The failure mode we are protecting
against is the one where the bar quietly drops until nothing fails, and by
then the bar has no relationship to the thing it was measuring.
