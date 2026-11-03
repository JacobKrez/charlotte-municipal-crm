# Architecture

The design we wrote for the City of Charlotte CRM modernization RFI, in
detail. `README.md` has the summary and the reasoning; this document has the
machinery.

Scope reminder: a case management platform for a city of about 1.2 million
people, spanning roughly 28 connected back-office systems, with a 311
contact center at the front door handling about 925,000 calls a year. It
integrates with the systems the city already runs. It replaces none of them.

**This is a design, not a running system.** Nothing here was deployed.

---

## 1. The shape of the thing

Seven layers, each with one job, and the rule we used to keep them from
overlapping: a layer may depend on the layers below it, and a layer may not
reach around a layer below it to shortcut the path.

```
Layer 1  Resident facing
Layer 2  Case management engine
Layer 3  AI brain
Layer 4  Knowledge management
Layer 5  Analytics and reporting
Layer 6  Platform and infrastructure
Layer 7  Extensibility, multi-tenancy and admin
```

Layer 6 governs layers 1 through 5 and layer 7 shapes them. It is a
deliberate asymmetry: security and extensibility are cross-cutting, not
features that live in one place.

### Why seven and not four

A smaller decomposition would have been easier to describe and harder to
build. The distinction that forced the extra layers is between *knowledge*
(layer 4) and *inference* (layer 3). They have different lifecycles: an
article has an owner, a review date, and a publication state, while an
inference has a model version, a prompt version, and a confidence score. Folding
them together makes the review workflow impossible to express, and it is the
part of the system that cities most often get wrong, because content
ownership is an organizational problem that no amount of model quality
solves.

The other split is between *operational* dashboards (layer 2, reading the
live case store) and *analytical* reporting (layer 5, reading the lake).
These have different latency requirements, different consistency
requirements, and different audiences. Putting them in one place produces
dashboards that are either slow or wrong.

---

## 2. Data flow

### 2.1 The write path

Every case write lands in one place, in one transaction, and everything else
is derived.

```
Resident channel  ->  API gateway  ->  FastAPI application layer
                                            |
                                            v
                              Aurora PostgreSQL (primary, us-east-1)
                              PostGIS for geography, pgvector for embeddings
                                            |
                        +-------------------+-------------------+
                        |                   |                   |
                  audit_log          EventBridge            Redis / SQS
                  (append only)     (async events)        (timers, queue)
                        |                   |
                        v                   v
                  S3 Object Lock      Boomi processes / webhooks
                  (signed chains)     (back-office integrations)
```

Two properties of this path are load-bearing.

**The audit log is written in the same transaction as the case.** An audit
record that is written by a background job is an audit record that will be
missing exactly when something goes wrong. The table has no update or delete
permission for the application role, so immutability is enforced by the
database rather than by convention.

**The application's answer does not wait for the integrations.** A case is
created and confirmed before the work order exists in the work-order system.
Integration events queue and retry; the case record does not roll back
because a downstream system is slow. This is why the integration queue is
durable for a long window, and why we consider a full backlog an incident
rather than a slow day.

### 2.2 The read path

Two read paths, and the distinction is not academic.

**Operational reads** (an agent opening a case, a supervisor watching a
queue) hit Aurora read replicas directly. Sub-second, strongly consistent
enough for a human decision.

**Analytical reads** (a neighborhood equity trend, a council report) hit the
lake. Minutes to hours stale, which is fine for a trend line and useless
for an SLA countdown. Conflating them is how you end up with a reporting
workload competing with case-handling latency.

### 2.3 The reporting plane

This layer replaced a chain the city described to us: source systems into
internal SQL, then an ETL into a separate public SQL database, then a second
ETL into a geodatabase for spatial publishing. Three hops to publish one
dashboard, four for anything with a map.

```
Aurora PostgreSQL Global Database
        |
        |  change data capture
        v
S3 Parquet lake (date-partitioned, columnar, KMS-encrypted, Object Lock)
        |
        +--> Athena        (ad-hoc SQL, no infrastructure to provision)
        +--> dbt           (versioned transformations, tests, lineage)
        +--> Glue catalog + Lake Formation (row-level and column-level access)
        |
        v
Conformed marts (each documented, tested, versioned)
  case_facts          sla_facts          satisfaction
  equity_metrics      channel_mix        cost_per_case
  constituent_360     knowledge_usage    integration_health
        |
        +--> Tableau      (public-facing dashboards)
        +--> Power BI     (internal only)
        +--> Esri feature services and open data portal (spatial and public)
        +--> Open data API (CSV, OData, REST)
```

The marts are the contract. Tableau, Power BI, and Esri all read the same
conformed tables, so a published public dashboard and an internal dashboard
cannot disagree, because they are reading the same rows with different
suppression rules applied on the way out.

Suppression is applied once, at the mart layer, before anything is
published. PII redaction, sensitive-address masking, and regulated-record
exclusion are properties of the mart, not a step each publishing target
remembers to perform.

**Why not a warehouse cluster.** At this volume (925,000 calls and 694,000
service requests a year, five years of history) every conformed mart is well
under 100 million rows, which serverless SQL over columnar object storage
handles comfortably and a dedicated cluster handles expensively. The same
design scales up if the volume changes: a dedicated warehouse can be added
underneath without reshaping the pipeline, and the lake remains the source of
truth.

**Retention is enforced at the lake layer.** Object storage in compliance
mode makes retention provable rather than aspirational, and it is auditable
as an export. Retention schedules follow the state's general records
schedule, per record class.

---

## 3. Layer by layer

### Layer 1: Resident facing

The resident-facing surface is a responsive web portal (progressive web app,
installable, offline-capable, push notifications) plus the city's own CLT+
mobile app, which we integrate with rather than replace.

**CLT+ is the city's existing investment.** It has about 20,000 monthly
users. Replacing it with a portal of our own would discard a working
resident-facing product and every habit and review attached to it. The CRM
provides the data and the inference; CLT+ keeps the resident relationship.
The service catalog stays synchronized with the CRM through webhooks so the
two never disagree about what the city offers.

Every channel (portal, CLT+, voice, chat, SMS, email, in person, social)
produces one record. A resident who texts on Monday, calls on Wednesday, and
checks CLT+ on Friday is looking at one case with one thread.

Accessibility is a requirement, not a phase: WCAG 2.1 AA and Section 508,
with a third-party audit and a published accessibility statement before any
production data. Language coverage is English and Spanish at launch, with
more languages added through the same framework.

### Layer 2: Case management engine

One case model for every service request in the city.

**The case model.** A case carries: a unique identifier, a constituent link
or an anonymous flag, a service address with geographic coordinates, a case
type drawn from a hierarchical taxonomy, primary and secondary department
assignments, a status in a fixed workflow (draft, submitted, assigned, in
progress, pending information, resolved, closed, cancelled), a priority tier
with a service-level clock, team and individual assignment, the complete
communication history, attachments, the full audit trail, the source channel,
and links to external records.

**Routing, in three layers, always in this order.**

1. *Rule-based* (case type to department). Always available. This is the
   floor, not a fallback. It never goes down, it costs nothing to run, and
   it is what the system uses when nothing else is available.
2. *AI routing.* The model classifies the description into a department, an
   urgency, and a complexity, and returns a confidence score. High
   confidence routes directly. Low confidence suggests, and a human
   confirms.
3. *Geographic.* Address to council district, service area, and jurisdiction,
   which handles the city, county, and town boundaries that a routing table
   alone cannot.

On top of those: workload-weighted assignment within a team, detection of
several open cases at one address, multi-department assignment with a
primary and a secondary owner, and a monitor that flags cases the system
routed to the wrong place.

**Service levels.** Every case type has a response target and a resolution
target in business hours. A supervisor is notified at 75 percent of the
target, leadership at 100 percent, and breaches are logged with a cause so
the pattern is visible rather than anecdotal.

**Communications.** Every message lives on the case record, in every
channel. Outbound messages are templated; letters are drafted from the case
context and always reviewed by a person before they are sent. In a
government, a human approves outbound communication to a resident. We do not
see a reason to argue with that.

### Layer 3: AI brain

Read the section in the README about where inference runs and why. This
section is about what the model is actually asked to do.

**Capabilities, and the confidence discipline that goes with each:**

| Capability | What it does | What happens on low confidence |
|---|---|---|
| Intake extraction | Reads email, chat, voice transcripts, and documents; extracts address, name, case type, urgency | Case is created, fields are flagged for staff confirmation |
| Routing classification | Department, urgency, complexity, with a confidence score | Suggested, staff confirms |
| Duplicate detection | Embedding similarity over address and description within a 30-day window | Related case is surfaced, not merged |
| Pattern detection | Clusters of similar cases by area and type | Alert to a supervisor |
| Sentiment and urgency | Flags frustration and possible vulnerability | Priority handling suggested |
| Knowledge retrieval | Answers staff questions from published articles, with citations | Routes to a person for an answer |
| Letter drafting | Drafts closure correspondence from case context | Always reviewed and approved by staff |

**Three rules that constrain the layer.** No outbound message is sent
without a human approving it. Every answer that cites a source shows the
source. Every prompt, model choice, and guardrail setting is versioned
alongside the application code.

**Model selection is configuration, not architecture.** Tiers are defined by
the work they do (fast classification and extraction, reasoning for routing
and retrieval, long context for correspondence and translation), and the
model behind each tier is a configuration value. The promotion path for a
prompt or model change is: evaluation harness, then shadow mode against
production traffic with no user-visible change, then a small canary, then
full rollout, with rollback as a version revert.

**Guardrails** sit at the platform layer for content filtering, personal
data redaction, prompt-injection defense, and the separation of internal
from public-facing AI.

### Layer 4: Knowledge management

The city's institutional knowledge currently lives in people's heads and in
inconsistently maintained documents. The layer is designed around the
organizational problem, not the technical one.

* **Service catalog**: every service the city offers, with eligibility,
  required documents, fees, and expected timeline. One source of truth across
  CLT+, the portal, the chatbot, and the mobile app. Department owners own
  their own entries.
* **Article lifecycle**: draft, review, publish, with version history, a
  named content owner per article, and automated reminders when a review is
  overdue.
* **Linking to case types**: when an agent opens a case, the matching
  articles surface automatically, without the agent searching for them.
* **Semantic search**: embedding search over the published subset, so a
  staff member searching for a refund procedure finds it even without using
  the word refund, alongside exact lookup for case numbers, addresses, and
  names.
* **Search analytics**: what staff searched for and did not find. That
  query stream is the backlog for the knowledge team.

### Layer 5: Analytics and reporting

Covered in section 2.3. The parts worth adding here:

**Self-service reporting** so a department supervisor can build a report
without filing a request, with scheduled delivery and row-level security
applied automatically based on what the person is allowed to see.

**The equity view**, which we think is the most interesting capability in the
proposal and also the one with the most unresolved questions. Response time
and resolution rate broken down by council district, neighborhood, and
census-tract demographics. The demographic breakdown derives from
address-level geography rather than from individual attributes, so it
describes service coverage over an area rather than profiling residents. The
open question, which we flagged in the response rather than hiding, is that
the city has to define what it wants measured here; an equity metric nobody
agreed on is a metric that will be argued about instead of acted on.

**Survey and feedback** built in, not bought, so the trigger can be a case
closure or a call and the analysis lands next to the case data.

**Cost per case by type, department, and channel**, so cost per service is a
measurement rather than an opinion.

### Layer 6: Platform and infrastructure

**Identity**: SAML federation with the city's existing identity provider,
with SCIM provisioning and group-based role assignment. A single sign-on
across the city and the county departments that handle 311 requests.

**Access control**: role-based access across roughly twenty roles, plus
row-level security in the database keyed to the identity provider's group
claims, plus field-level masking for sensitive fields (domestic-violence
addresses are the canonical case), plus separation of duties between
application, administrative, and audit-reading principals, and no long-lived
credentials (short-lived tokens everywhere).

**Encryption**: AES-256 at rest with customer-managed keys at every
encryption boundary, TLS 1.3 in transit with 1.2 as the floor, and key
rotation.

**Audit, in three tiers**, because the city's requirement is not just
"log it" but "the logs must be tamper-evident":

1. Application events, written in the same transaction as the state change.
2. Signed log chains: each batch of events is hashed, the hash is signed
   with a key in the key management service, and the signed hash is
   published to immutable object storage and to the log archive. Each batch
   references the previous batch's hash, so altering historical audit data
   invalidates the chain at the next verification, which runs daily.
3. Platform-level audit, in a separate locked-down account, covering
   identity actions, key use, and protected object access.

Events stream to whichever SIEM the city designates, and investigators get
SQL access over the archived log through the lake.

**Retention** follows the state's general records schedule per record class,
with the default at seven years for case-related records and the ability for
a records officer to adjust without engineering involvement.

**DR and degraded mode**: see the README.

### Layer 7: Extensibility, multi-tenancy and admin

**Single tenant per city, shared codebase.** Each city gets a dedicated
cloud account with its own network, keys, database, storage, inference
policies, and audit trail. That makes the data boundary an account
boundary, which is the easiest kind for a government security review to
accept, and it satisfies the county co-tenancy requirement without a shared
database. The cost is real: every city is an infrastructure estate to
operate.

Every city runs the same versioned codebase. Releases are versioned and
scheduled: monthly stable, weekly security patches, an opt-in beta channel,
and a customer-controlled maintenance window. City-specific configuration
(the case type taxonomy, service levels, notification templates, integration
mappings, branding, roles) lives in a separate configuration repository, so
a configuration change does not need a code release and a code release does
not invalidate configuration.

**Within one city, multi-agency tenancy** is enforced in the application
layer: every record carries an organization identifier, row-level policies
restrict reads and writes by that identifier using identity group claims,
cross-organization referrals are an explicit record with an audit trail
rather than a silent move, and shared 311 queue scope is configurable per
case type. This is the part that matters for the county departments that
handle 311 requests, since they need direct access rather than a referral
that loses the context.

**Four extension surfaces**, each versioned and documented:

| Surface | Use it for | Contract |
|---|---|---|
| REST API | Anything a client application needs; this is what CLT+ uses | OpenAPI specification published with every release; a version supported for at least 24 months after its successor ships |
| Webhooks | Subscribing to domain events without polling; this is how the city's integration platform listens | HMAC-signed, at-least-once delivery, retries with backoff, dead-letter queue with admin replay |
| Python plugins | Workflow logic that does not fit the API model: a routing rule, a validation, a department-specific closure workflow | Versioned modules against a documented stable interface, sandboxed, failures surfaced rather than swallowed |
| YAML configuration | Case types, service levels, templates, dashboards, surveys, roles, integration mappings | The no-code admin interface commits to the same files, so a UI change is a reviewed change |

**The no-code admin interface** turns the common configuration tasks into
forms: case types, service levels, notification templates with per-channel
preview, article lifecycle, survey designer, dashboard builder, integration
mapping, role management, retention rules. Every admin action is audited
with a before-and-after diff, and every configuration change goes through a
review, whether it was made in the interface or in a file.

---

## 4. Integration architecture

Three tiers, assigned by latency rather than by preference.

### Tier A: direct (8 systems)

| System | Method | What crosses |
|---|---|---|
| Telephony and IVR | Direct CTI | Screen pop on inbound call, caller identification, call control, call logging; IVR context forwarded by signed token |
| CLT+ | REST plus webhooks | Cases in, status out, service catalog synchronized by event |
| GIS platform | Native bidirectional | Address validation against the county source of truth, district and service-area lookup, heat maps; the CRM publishes spatial data out |
| Tableau | Native database/OData | Analytics replica for public dashboards |
| Power BI | OData | Analytics replica for internal-only use, preserving the city's public and internal split |
| Office calendar | Graph API | Bidirectional calendar sync, meeting invites, appointments |
| Identity provider | SAML 2.0 | Federation, SCIM provisioning, group-based roles |
| Payments | Webhook | Confirmations only. Card data is captured, transmitted, and stored entirely by the payment provider |

### Tier B: open standard

Open311 GeoReport v2, so any compliant third-party application can submit
a service request with no custom integration work.

### Tier C: Boomi-orchestrated (the remaining back-office systems)

Work orders, utility billing, HR and manager hierarchy, records management,
public records requests, permitting, towing, internal IT ticketing,
civic engagement, project boards, procurement, code enforcement, payment
confirmations, and the remaining enterprise systems.

The reason is not that Boomi is a better integration tool than what we would
otherwise build. The reason is that the city already operates it, it owns
the connections, the runtime, the retries, and the credentials, and a
migration can consolidate the older middleware onto it as part of the same
program.

**What this means for delivery, stated plainly.** The application track is
ours to schedule. The back-office integration track is bounded by the
capacity of the city's integration team to design processes jointly with
us. We can publish the API contracts for all 28 systems early so the vendor
conversations can start, and we can ship the application work regardless,
but we cannot honestly promise a date for the last back-office integration
that is also entirely under our control.

**No city system requires replacement.** The CRM is a hub over the existing
estate, not a replacement for it.

---

## 5. The AI hosting decision, in full

### The decision

All inference runs on Amazon Bedrock inside the city's own account, in a
commercial region, with a separate restricted tier in GovCloud available for
any workload that brings regulated data into scope.

### What it buys

| Requirement | How the decision satisfies it |
|---|---|
| Inference stays in the resident's jurisdiction | Model invocations execute in the region we deploy into. No prompt, transcript, or case content crosses to other infrastructure. |
| City data is never used to train models | The platform's service terms contractually exclude customer prompts and completions from training any underlying model. The enforcement is at the platform layer rather than in our contract. |
| Inference is auditable | Every invocation is logged with model identifier, token counts, identity principal, and request identifier. Retained, queryable, exportable to a SIEM. |
| Features can be disabled without a code change | Policy boundaries scoped per role and per feature. Turning off a capability is a policy update. |
| Administrative access is restricted | Model access, guardrail configuration, and batch jobs require multi-factor authentication and are limited to personnel based in the United States. |
| Surge capacity | Per-token inference absorbs a storm surge without a permanent accelerator fleet. |

### What it costs

* **We give up running a fine-tuned model on city data.** Bedrock supports
  customization, but our proposal did not depend on it and we did not
  promise it. For a workload that genuinely needs a fine-tuned model, a
  self-hosted fleet becomes the better answer, and we set out in the
  response the bar such a workload would have to clear.
* **Inference is a recurring metered cost rather than a fixed one.** The
  risk is that a traffic spike costs more than we planned. We treat this as
  a budget line with a ceiling and a circuit breaker, not as a surprise.
* **We inherit the platform's model catalog.** We do not control when a
  model version is retired, which is a real operational dependency and the
  reason the design treats model choice as configuration with a promotion
  gate.
* **The city owns the account, which cuts both ways.** We cannot deploy
  unilaterally, and neither can the city without us. That is the correct
  posture for a security review and it does slow the roadmap.

### The alternative we rejected

A self-hosted accelerator fleet running open-weight models.

Rejected on three grounds, in order of weight:

1. **Cost at this scale.** With 925,000 calls and 694,000 service requests
   a year, serving inference from a fleet large enough for acceptable
   availability costs more than per-token inference at the same scale. The
   fleet also has to be sized for peaks, not averages.
2. **Operational surface.** A fleet is a patch surface, a driver
   vulnerability surface, and a model-weight integrity surface. Some of
   those concerns are handled by the platform's responsibility model and
   some are not.
3. **Upgrade mechanics.** A platform model upgrade is an API version bump
   with a rollback. A fleet upgrade is an infrastructure project.

The honest counter-argument, which we made in the response rather than
hiding: a self-hosted fleet gives more control, removes the metered cost,
and allows a fine-tuned model. If the city has a workload that needs a
fine-tuned model or has a policy reason to require self-managed inference,
that changes the answer. We would revisit it for a specific workload, not
as a general preference.

---

## 6. Failure modes

Every layer has a defined failure. The design rule is that a resident
calling about a pothole never receives an error message about a model.

| Failure | What happens | Why the resident experience survives |
|---|---|---|
| Inference service unavailable | Fallback chain: faster model, then rule-based, then tone menu. Circuit breaker opens and the interface says assistance is temporarily unavailable. | Routing is rule-based first, so a case still reaches a department. The caller hears a tone menu, not an error. |
| Model returns low confidence | Routing falls to suggestion, staff confirm. Extraction flags fields for confirmation. | No case is created with invented details, because confidence gates creation. |
| Integration platform unavailable | Outbound events queue durably and retry. Inbound events resume on recovery. | Case creation does not depend on a downstream work order existing. |
| Primary region unavailable | Traffic shifts to the secondary region, the database secondary is promoted, and the status page updates. | Quarterly drill measures the actual time to recover against the target. |
| Identity provider unavailable | Existing sessions continue for their lifetime, break-glass administrative access exists, anonymous submission paths stay open. | A resident can still submit a request without signing in. |
| Audit log service failure | An alarm fires, writes fail over to a local journal that reconciles on recovery, and a gap over five minutes becomes an incident. | The gap is detected and bounded rather than discovered months later. |
| Payment provider unavailable | Payment branches in the existing IVR handle the call, and no card data is in the CRM to lose. | The blast radius of a payment outage is the payment provider. |
| Batch analytics fails | Deferred to the next successful schedule, and the supervisor is told the analytics are stale. | Nothing a resident sees depends on the batch. |
| Guardrail misconfiguration | A misconfiguration is visible in the per-feature health indicator, and features can be disabled by policy. | A feature that misbehaves can be turned off without a deployment. |

The quarterly drill rotation exercises the full failover, a simulated
inference outage, the voice failover paths, audit chain verification with a
synthetic tampering attempt, a point-in-time database restore, and an
attempted deletion of a retention-locked object. Results are written up and
shared with the city, and any miss against the recovery targets produces a
remediation plan with a date.

---

## 7. What this design does not solve

* **It does not make the 311 queue shorter by itself.** It makes each
  interaction cheaper and more informed. Staffing is a city decision.
* **It does not fix knowledge that nobody has written down.** The layer
  gives content owners a workflow. Somebody still has to write the article.
* **It does not resolve the equity metric definition.** We built the
  machinery. The city has to decide what equitable service means for
  Charlotte, and that is a policy question.
* **It does not retire the city's older middleware.** Consolidating onto
  the current integration platform is part of the modernization story, but it
  is a separate program with its own schedule.
* **It does not make the model correct.** It makes the model's mistakes
  bounded, detectable, and recoverable. A system with a fallback chain and
  confidence gates is safer than a system without them, but it is not a
  system that cannot be wrong.
