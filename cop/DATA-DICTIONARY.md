# Legate COP data dictionary

**Authority:** COP-2 WP-0, amended by COP-2-AMD1.  
**Status:** source contract for COP-2. A rail badge, clock, or exception field must have a
row here before it is rendered.

COP means Common Operating Picture. C2 means command and control. DSOR means Decision
System of Record. SLA means service-level agreement.

## Reading rules

1. The only disposition colours are kernel `Disposition` values — `PASS`, `HOLD`, `BLOCK`
   and `INDETERMINATE` — plus the visibly hatched `Absent` state.
2. `Absent` is the kernel `Absent` evidence value, not zero, an empty collection, or a grey
   version of `PASS`. A source that cannot be read never supplies a zero.
3. Status text such as *Investigating*, *Monitoring*, *Awaiting decision*, *Review due* and
   *Written off* appears under a disposition. It never selects a colour.
4. A composite is no greener than its weakest current input. Stale last-good values may be
   shown as stale evidence but do not determine the current disposition.
5. Layer clocks come only from kernel `EventTimes` in published records. Venue windows come
   from a published cutoff table. Neither is derived from the COP process's wall clock.
6. Missing producers render `Absent` in production. Synthetic values exist only with
   `LEGATE_DEMO=1`, and every synthetic tile identifies itself as demo data.

## Source register

| Key | Repository and published document | Current state |
|---|---|---|
| `program` | `br-collab/L.C.`, packaged `cop/program.yaml` | Published with Legate |
| `github` | GitHub REST documents for commits, workflow runs, tags and pull requests | Live |
| `aureon-snapshot` | `br-collab/aureon`, public `/api/snapshot` | Live; no kernel `EventTimes` |
| `cash-leg` | `br-collab/aureon`, public `/api/cashleg/demo` document prepared by Atreides | Reachable demonstration document; admitted only in `LEGATE_DEMO=1`. Production has no cash-leg producer, cutoff table or clocks |
| `agents` | `br-collab/Project-Atreides`, activation snapshot configured by `ATREIDES_AGENTS_URL` | Producer exists; deployment URL may be absent |
| `c2-escalations` | `br-collab/L.C.`, C2 escalation queue configured by `C2_ESCALATIONS_URL` | Reader and demo producer exist; live publication may be absent |
| `lifecycle` | Published lifecycle document assembled from Aureon, L.C. and Atreides records | No production producer; demo only |
| `breaks` | Atreides cross-layer break records | No producer |
| `holds` | Aureon hold records | No aggregate producer |
| `dsor` | Aureon DSOR authority-decision feed | No published feed |
| `controls` | Aureon/Verana screening outputs and control-test results | No aggregate producer |
| `limits` | Atreides cash exposure plus Aureon Kaladan limit definitions | No published limit definitions |
| `cutoffs` | Published trading-session, Fedwire and NSCC cutoff table | No producer |
| `ofr` | Published Office of Financial Research stress reading used in production | No COP feed; test and production series difference must remain visible |

## Side-rail badges

The badge value is supporting text (usually a count). The badge colour always comes from the
listed kernel disposition rule.

| Rail item | Kernel type and rule | Source key(s) | If unread or absent |
|---|---|---|---|
| **Now** | Worst kernel `Disposition` of current items requiring authority, changes, and stale clocks/feeds; count is `Recorded[int] \| Absent` | All section sources below | `Absent` when no required source is readable; never “0 needs you” |
| **Trades** | Worst `Disposition` of `LifecycleRow`, whose cells represent `ApprovedIntentEnvelope`, `ExecutionEvent`, `ClearingTransformation`, `SettlementObligationEnvelope` and `ObligationAcceptanceRecord` | `lifecycle` | Hatched `Absent`; demo rows only in demo mode |
| **Exceptions** | Worst exception `Disposition`; count is `Recorded[int] \| Absent`. No owner forces `BLOCK` | `breaks`, `c2-escalations`, `holds`, `dsor` overrides | Hatched `Absent`; never an empty register presented as zero |
| **Cash & liquidity** | Worst `Disposition` of funding, cutoff evidence and exposure-versus-limit; supporting count is `Recorded[int] \| Absent` | `cash-leg`, `cutoffs`, `limits` | Hatched `Absent` in production until all required producers publish. `/api/cashleg/demo` values appear only in demo mode |
| **Decisions** | Worst `Disposition` of C2 escalations and DSOR authority records; count is `Recorded[int] \| Absent` | `c2-escalations`, `dsor` | `Absent` if neither feed is readable; partial coverage is `INDETERMINATE`, not `PASS` |
| **Controls & compliance** | Worst `Disposition` of recorded control results. No test evidence is `INDETERMINATE` | `controls`, relevant `github` control-test runs | Hatched `Absent` when no control inventory is published; missing evidence is never passing |
| **Risk limits** | Worst `Disposition` of `Recorded` exposure and `Recorded` limit; utilisation bands are policy results at 80%, 90% and 100% | `limits`, `cash-leg` | Hatched `Absent` until limit definitions publish; exposure alone cannot produce utilisation |
| **Agents** | `AgentsSnapshot.disposition`, itself kernel `Disposition`; supporting text is recorded running/total count | `agents` | Hatched `Absent` when not configured; `INDETERMINATE` when configured but unreadable or stale |
| **Programme** | Worst `Disposition` of program status, repository main CI, scheduled checks, deploy drift and source freshness | `program`, `github`, `aureon-snapshot` | `INDETERMINATE` for an unread source; never green from the remaining subset |
| **Blind spots** | Each entry is `Absent` evidence or a source/layer/contract limitation; count is `Recorded[int]` and is invariantly greater than zero | Computed from every source above plus the maintained contract-gap list | The rail item itself remains visible. Failure to compute the list is `INDETERMINATE`, never zero |

## Clock strip

`event_time`, `observation_time`, `processing_time` and optional `decision_time` keep their
distinct meanings from kernel `EventTimes`. The strip displays the latest record's
`event_time` explicitly; it does not reconcile clocks.

| Clock | Kernel type | Repository and published document | If unread or absent |
|---|---|---|---|
| **Aureon** | `EventTimes` from the newest current `ApprovedIntentEnvelope` record | `aureon`; future `lifecycle` document | Hatched `Absent`. `/api/snapshot` has no `EventTimes` and may not substitute its fetch time |
| **L.C.** | `EventTimes` from the newest current `ExecutionEvent`, `ClearingTransformation` or `SettlementObligationEnvelope` | `L.C.`; future `lifecycle` document | Hatched `Absent` while the Wave 4 layer or producer does not exist |
| **Atreides** | `EventTimes` from the newest current `ObligationAcceptanceRecord` or settlement record | `Project-Atreides`; future `lifecycle`/settlement document | Hatched `Absent`; activation `taken_at` is feed freshness, not the settlement-layer clock |
| **Trading session** | `Recorded[MarketSession] \| Absent`, paired with the source record's `EventTimes` | Future published session document; not local time | Hatched `Absent` until published |
| **Fedwire state and close** | `Recorded[str] \| Absent` state and `Recorded[datetime] \| Absent` close from a versioned cutoff table | `cutoffs` | Hatched `Absent`; do not calculate from wall time or copy a remembered schedule |
| **Next NSCC cycle** | `Recorded[datetime] \| Absent` from a versioned cutoff table | `cutoffs` | Hatched `Absent`; do not calculate from wall time |
| **Last full refresh** | COP observation `Recorded[datetime]`; not a domain `EventTimes` clock | Legate refresher after every configured source answers | `Absent` until one clean refresh completes; retain a stale value only with an explicit stale label |

Every external feed also exposes its last-good-reading age under **Blind spots**. That entry is
`INDETERMINATE` when stale and `Absent` when never read. The `ofr` feed entry must state the known
Cato test-versus-production series difference on the glass.

## Exception register and drawer

The production register remains `Absent` until the required producers publish. The types below
define the reader contract; they do not authorize Legate to invent or write any field.

| Field | Kernel type or kernel-backed rule | Repository and published document | If unread or absent |
|---|---|---|---|
| **Exception ID** | `Recorded[str] \| Absent`; non-empty and stable in its producer | `breaks`, `c2-escalations`, `holds`, or `dsor` | The record is not admitted without an ID; the register source is malformed and `INDETERMINATE` |
| **Kind** | `Recorded[str] \| Absent`: Break, Escalation, Hold or Override | Same producer as the exception | `Absent`; never inferred from wording |
| **Lifecycle/trade ID** | Kernel lifecycle identifier carried by the relevant envelope or C2 packet, represented as `Recorded[str] \| Absent` at the reader | Same producer; `EscalationPacket.lifecycle_id` for C2 | `Absent`; do not join on a guessed identifier |
| **Title/summary** | `Recorded[str] \| Absent` | Same producer; `EscalationPacket.summary` for C2 | `Absent`; do not synthesize a cheerful fallback |
| **Root-cause class** | `Recorded[str] \| Absent` | Exception producer | `Absent`; “unknown” is displayed as absence, not a class |
| **Disposition** | Kernel `Disposition` | Producer disposition, then COP fail-closed rules | `INDETERMINATE`; a missing owner overrides it to `BLOCK` |
| **Status text** | `Recorded[str] \| Absent`; presentation text under `Disposition`, never a colour | Exception producer | `Absent`. Written off is distinct from resolved and retains its own disposition |
| **First recorded time** | Kernel `EventTimes.event_time` from the first record that states the exception; its observation and processing times remain available in the trail | Exception producer | `Absent`; SLA age and time left also become `Absent` |
| **First recorded layer** | `Recorded[str] \| Absent`, naming the layer that supplied those `EventTimes` | Exception producer | `Absent`; the COP observation clock may not substitute |
| **Age** | Policy result `now - first EventTimes.event_time`, represented as `Recorded[timedelta] \| Absent` | Computed only after the first record is read | `Absent`; never starts from when the COP noticed the exception |
| **SLA target** | `Recorded[timedelta] \| Absent` from a published, versioned SLA table referenced by the exception | Future SLA publication alongside exception sources | `Absent`; age may display but “within SLA” may not |
| **Time left / past target** | Policy result `SLA target - age`, `Recorded[timedelta] \| Absent` | Computed from the two preceding recorded values | `Absent` if either input is absent or stale |
| **SLA utilisation** | Policy result `age / target`, `Recorded[decimal] \| Absent`; amber at 75%, red past 100% | Computed | `Absent` if either input is absent; no zero-width success bar |
| **Owner** | Kernel `ActorRef` wrapped as `Recorded[ActorRef] \| Absent` | Exception or DSOR actor record | Missing owner is shown as `Absent` evidence and forces the exception disposition to `BLOCK` |
| **What closes it** | `Recorded[str] \| Absent`; a specific closure condition, not an action in Legate | Exception producer | `Absent`; the drawer never invents a remedy |
| **Authority link** | `Recorded[str] \| Absent` URI to the system where authority lives | C2 packet or DSOR publication | Link omitted and field shown `Absent`; Legate never substitutes a write control |
| **Written-off marker** | `Recorded[bool] \| Absent` backed by an `AuthorityRecord`/DSOR decision | `dsor` | `Absent`; never inferred from a status string. Written off is counted separately from resolved |
| **Resolution record** | `Recorded[AuthorityRecord] \| Absent` or other published domain closure record | `dsor` or exception producer | `Absent`; an exception cannot be counted resolved without it |
| **Trail layer** | `Recorded[str] \| Absent` for each trail entry | The published record supplying that entry | Entry omitted if its source identity is malformed; expected-but-missing layer is an explicit `Absent` entry |
| **Trail time** | Kernel `EventTimes` for each trail record | The published record supplying that entry | `Absent`; never replaced by COP observation time |
| **Trail state** | Kernel `Disposition` plus separate status text | The published record supplying that entry | `INDETERMINATE` when unreadable; expected-but-unbuilt layer is `Absent` |
| **Trail evidence/provenance** | Kernel `Provenance` and `Recorded[str] \| Absent` evidence reference | The published record supplying that entry | `Absent`; no unsupported narrative is displayed |

## Exception-health derivations

These panel-14 values are derived only from current exception records admitted under the table
above. If the register is `Absent` or unreadable, every health value is `Absent`, never zero.

| Value | Kernel-backed rule | Inputs | If unavailable |
|---|---|---|---|
| Age buckets (<1h, 1–4h, 4–24h, >24h) | `Recorded[int] \| Absent` counts over recorded age | First record `EventTimes` | All buckets `Absent` |
| Resolved within SLA | `Recorded[decimal] \| Absent`; written-off records excluded from resolved numerator and reported separately | Resolution record, first record time, SLA target | `Absent` if any required population field is unavailable |
| Written-off count | `Recorded[int] \| Absent` | DSOR-backed written-off marker | `Absent`; never folded into resolved |
| Repeat root causes | `Recorded[tuple[str, ...]] \| Absent` grouped by recorded root-cause class | Root-cause class and stable exception ID | `Absent`; no grouping of unknown causes |
| Trend | `Recorded[tuple[int, ...]] \| Absent` over a published reporting cadence | Versioned exception history | `Absent` until history is published; do not reconstruct from process memory |

## Permanent governance statement

While CAOM-001 applies, every page displays exactly: **“Single operator: no separation of
duties.”** This is policy text, not a disposition and not evidence that a second reviewer exists.

## Rendered-panel reconciliation

This register closes the display-to-source audit for the fields outside the exception and
governance/risk registers above. “Absent” is a real source result: it means that production has
no qualifying producer, not that the value is zero or passing.

| Panel and rendered field or badge | Type or rule | Real source, or Absent |
|---|---|---|
| **Banner — overall program state badge and reasons** | Worst current kernel `Disposition` across main CI, scheduled checks, Aureon snapshot/drift/pending-drop evidence, blocked work, agent activation and source freshness | `program`, `github`, `aureon-snapshot`, `agents`; `INDETERMINATE` when any required input is unreadable |
| **Banner — last clean refresh, last attempt, page rendered, refresh interval** | COP observation datetimes and configured interval; never domain `EventTimes` | Legate refresher and settings; last clean refresh is `Absent` until one clean refresh completes |
| **Now — needs you, changed, stale clocks/feeds** | Overall reasons; exception-source badge; source freshness | Same inputs as Banner, plus `breaks`, `c2-escalations`, `holds`, `dsor`; change history is `Absent` without a producer |
| **Wave board — wave/work identifier, name, status, packages, evidence, next action and owner** | Versioned programme records; status badge uses the declared work status, not a domain disposition | `program` (`cop/program.yaml`) |
| **Repositories — main commit** | Recorded Git commit Secure Hash Algorithm identifier | `github`; `Absent` when unreadable |
| **Repositories — CI on main and workflow rows** | Worst recorded workflow `Disposition`, workflow name, status/conclusion and link | `github`; `INDETERMINATE` when checks cannot be read |
| **Repositories — latest tag** | Recorded tag name or recorded no-tags result | `github`; `Absent` when unreadable |
| **Repositories — pull request number/title/draft, opened time, age, checks and merge state** | GitHub pull-request record; age derives from its GitHub creation clock | `github`; `Absent` when unreadable, never an invented empty list |
| **Live Aureon — snapshot badge** | Snapshot freshness and shape disposition | `aureon-snapshot`; `INDETERMINATE` when stale or malformed |
| **Live Aureon — deployed commit, stack, positions, pending decisions, market open** | Recorded snapshot fields; each field has its own observed/absent badge | `aureon-snapshot`; any missing field is `Absent` and is not inferred |
| **Live Aureon — deploy drift badge and compared commits** | Policy result comparing recorded deployed commit with GitHub `main` | `aureon-snapshot`, `github`; `Absent` if either commit is unavailable |
| **Live Aureon — pending-drop badge, comparison count, before/after pending and commits, detection time** | Process-local AUR-I-17 observation; detection time is explicitly a COP clock | `aureon-snapshot` observations held by Legate; `Absent` before comparable observations exist |
| **Agents — aggregate badge, phase, synthetic marker, halt state and activation tick** | `AgentsSnapshot` fields and disposition; tick is producer freshness, not a settlement clock | `agents`; `Absent` when the endpoint is not configured |
| **Agents — identifier, tier, role, output, observation time, claim kind, handoff basis/refusal and counts** | Recorded activation-agent fields; optional handoff/refusal values are `Recorded \| Absent` | `agents`; missing optional evidence stays hatched `Absent` |
| **Lifecycle — board/row badge, lifecycle identifier and unknown count** | Worst checkpoint `Disposition`; kernel lifecycle identifier | `lifecycle`; production is `Absent` until a document is published |
| **Lifecycle — checkpoint, layer, detail, disposition, provenance and stamped time** | The five frozen envelopes plus settled state; source-layer `EventTimes` only | `lifecycle`; unbuilt L.C. checkpoints are `Absent`, never PASS |
| **Escalations — queue/packet badge, packet and lifecycle identifiers, trigger, summary, raised/waiting, findings and unknowns** | Published `EscalationPacket`; waiting derives from packet `raised_at` | `c2-escalations`; production is `Absent` when unconfigured |
| **Breaks — panel/row badge, object, left/right claims and layer clocks** | Cross-layer break record with producer dispositions and producer timestamps | `breaks`; no production producer, demo only |
| **Cash leg — panel badge, scenario, boundary and funding disposition** | Demonstration document disposition and text | `cash-leg`; admitted only in `LEGATE_DEMO=1`, otherwise `Absent` |
| **Cash leg — rail, finality class and net-debit-cap headroom** | Recorded demonstration-document fields | `cash-leg`; demo only, otherwise `Absent` |
| **Cash leg — cutoff headroom, rail clock and trading-session clock** | `Recorded \| Absent`; no wall-clock calculation | `cutoffs` and future cash-leg producer; currently `Absent` even in the demo response because the endpoint does not publish them |
| **Blind spots — kind badge, name, detail and remedy** | Computed source/layer/contract limitation; count cannot be zero | Every registered source plus maintained layer/contract gaps |
| **Scheduled checks — repository, workflow, run badge and run time** | Latest scheduled GitHub Actions run and its GitHub clock | `github`; `Absent` when unreadable |
| **Open decisions — panel badge, identifier, title, owner, opened date and age** | Versioned programme decision; age derives from its recorded opened date | `program`; an unreadable programme file is `Absent`, not “no decisions” |

### Reconciliation result

- **Rendered panel without a dictionary row:** none after this reconciliation.
- **Dictionary row without a rendered panel:** none. Source-register entries such as `holds`,
  `dsor`, `controls`, `limits`, `cutoffs`, and `ofr` feed rendered composite panels or Blind
  spots; they are not promised as standalone panels.
- **Known absent producers:** lifecycle, breaks, holds, DSOR, controls, limits, cutoffs, the
  production cash leg, and a COP OFR feed remain Absent as stated in the source register.

## Governance, controls and risk panels

These panels admit demo records only under `LEGATE_DEMO=1`. Production remains `Absent` until
the source register names a published producer.

| Field | Kernel type or kernel-backed rule | Source key | If unread or absent |
|---|---|---|---|
| **Governance event** | Stable `Recorded[str]` ID and event kind | `dsor`, `aureon-snapshot`, `github` | Panel `Absent`; no empty audit trail |
| **Decision actor** | Authenticated kernel `ActorRef` | `dsor` | Event rejected; actor is never inferred |
| **Decision time** | Kernel `EventTimes`, displaying `decision_time` when recorded | `dsor` | `Absent`; COP observation time never substitutes |
| **Doctrine and evidence** | Recorded doctrine version and evidence references with kernel `Provenance` | `dsor` | `Absent`; no unsupported decision narrative |
| **Control result** | Kernel `Disposition` plus separate status text | `controls` | `INDETERMINATE` when test evidence or its `EventTimes` is absent, never `PASS` |
| **Regulatory mapping** | Recorded control-to-rule references | `controls` | `Absent`; no inferred mapping |
| **Risk exposure and limit** | Recorded `Decimal` values sharing a unit | `limits`, `cash-leg` | Panel `Absent` unless both are published |
| **Risk utilisation** | `exposure / limit`; `PASS` below 80%, `HOLD` at 80–99.99%, `BLOCK` at or above 100% | Computed only from recorded exposure and limit | `Absent`; an exposure without a limit is not zero utilisation |
| **Risk clock** | Kernel `EventTimes` from the source measure | `limits`, `cash-leg` | `Absent`; local wall time never substitutes |
