# Revrec Impact Assessment Meters Vs Contracts

Source type: google_drive
Document ID: dsid_4587f467ecf646b7926dbd6b886f9f23
Release: v1.0.0
Benchmark: EnterpriseRAG-Bench synthetic upstream data
RevRec Impact Assessment: Meter Changes vs Contracts (Hosted API + Dedicated) — Feb 2026

Purpose
This document assesses revenue recognition implications of ongoing metering changes across Hosted API and Dedicated. It is intended to (1) guide whether meter fixes trigger contract modifications or variable consideration re-estimation, (2) define materiality thresholds for action, and (3) document the policy for retroactive adjustments, disclosures, and audit trail requirements.

This is not a full accounting memo, but it is meant to be “audit-ready” in terms of decision logic, approvals, and evidence retention.

Scope (meter changes under review)
Hosted API (usage-based):
- Token counting alignment across endpoints (incl. OpenAI-compatible endpoints)
- Double-counting prevention for server-side retry/fallback paths (esp. streaming)
- Streaming partials + disconnect behavior (when tokens are considered “delivered” vs “generated”)
- Embeddings metering for batched requests
- Cache-hit attribution fields and billed_tokens vs raw_tokens exposure

Dedicated (reserved capacity + usage/burst):
- GPU-seconds meter v2 (idle accounting, autoscaling transitions, burst/overage rules)

Primary risks addressed
1) Over-billing or under-billing due to incorrect meters
2) Revenue recognition errors (variable consideration / constraint / estimates)
3) Increased disputes and credit issuance without consistent accounting treatment
4) Audit evidence gaps (why a meter changed, when, approvals, shadow-billing comparisons)
5) Distorted pricing experiment results that could influence forecast / rev planning

Accounting frame (practical summary)
- For Hosted API usage-based arrangements, revenue is generally recognized as usage occurs (variable consideration based on actual measured consumption). If the meter is wrong, the key question becomes whether we have:
  a) a billing error (incorrect measurement) requiring a correction/credit, vs
  b) a change in the definition of the unit of account/usage (contractual definition) that could be a contract modification or change in estimate.

- Our default posture: meter changes are presumed to be corrections to measurement (billing error correction) unless Legal confirms the contract language supports the prior behavior as an acceptable interpretation.

- Dedicated: revenue recognition is primarily based on committed consideration (reservation/commit). Usage metering affects overages/burst and can affect allocation for variable components, but the core commit is less sensitive. However, mis-metered overages can still create revenue errors and disputes.

Contract interpretation: “what does the customer buy?”
We need to map meter changes to contract usage definitions.

Hosted API typical contracted unit:
- “Tokens processed” (or “billed tokens”) as defined in the Order Form / pricing exhibit.
- Key ambiguity points that matter for meter changes:
  1) Are tokens counted on attempted generation or delivered output?
  2) How are retries handled (client retries vs server retries)?
  3) Are cache hits billed? If yes/no, how is “cache hit” defined?
  4) How are tool/function calls and structured output tokens treated?
  5) Streaming: do partials count if stream disconnects?

Dedicated typical contracted unit:
- Monthly committed reservation (fixed)
- Variable component: overage GPU-hours/GPU-seconds, burst, or “on-demand usage” depending on the order form
- Key ambiguity points:
  1) Idle time billing (warm pools, scaling transitions)
  2) Definition of “GPU-seconds” (wall clock vs active compute)

Meter change classification (RevRec view)
Class A — “Measurement correction” (default)
- Fixes double-counting, duplicates, missing dimensions, incorrect batching logic, or inconsistent computation.
- Typically treated as correction of an error in measurement/billing. May require credits/refunds if customer was overcharged and the effect is material (see thresholds below).
- Requires strong audit trail and change control.

Class B — “Clarification/interpretation shift”
- The meter changes because we adopt a new interpretation of ambiguous language (e.g., previously billed server retries, now do not) without explicit customer contract update.
- Requires Legal sign-off. If new behavior is more favorable to the customer, we can implement prospectively; retroactive application requires Finance/Legal decision and potentially consistent treatment across similarly situated customers.

Class C — “Commercial change”
- Packaging/pricing definition changes (new unit definition, new included usage, new free tier behaviors) applied to existing customers.
- This is generally a contract modification / new pricing exhibit. Requires Order Form update or explicit notice/consent per contract.

Current project items mapped to classification (preliminary)
- Fix double-counting on retry/fallback (streaming and non-streaming): Class A
- Embeddings batching bug fix: Class A
- Add missing region dimension: Class A (measurement enrichment; should not change totals if logic was correct, but can affect price if region-based pricing applies)
- Align token counting for OpenAI-compatible endpoints: Likely Class A if we are fixing inconsistency relative to documented spec; Class B if the spec is ambiguous and behavior changes total billed usage
- GPU-seconds v2: Could be Class A or B depending on whether prior idle accounting was an “error” vs an allowed method. Treat as Class A only if we can demonstrate prior calculation deviated from our written spec / customer-facing definition.

Materiality: thresholds and decision rules
We are using two sets of thresholds: (1) customer-level billing remediation decisions, and (2) financial statement / audit materiality.

1) Customer-level remediation thresholds (credits/adjustments)
Applies when meter changes indicate historical overbilling/underbilling.
- Tier 1 (auto-remediate):
  - Overbill amount >= $250 AND >= 2% of the customer’s monthly invoice for any billed month in scope
  - OR customer has opened a dispute ticket regardless of $ (to resolve support escalation)
  Action: Issue credit on next invoice (preferred) or refund if required by contract; document in ticket + billing adjustment log.

- Tier 2 (manager review):
  - Overbill amount between $50–$250 OR <2% but recurring across >= 3 billing periods
  Action: Finance review; default credit if easy to calculate and customer-visible impact is likely.

- Tier 3 (no retroactive action; prospective fix):
  - Overbill amount < $50 AND non-recurring AND no customer dispute
  Action: No retroactive adjustment; document rationale (immaterial) and apply fix prospectively.

Underbilling:
- Default: do NOT retroactively invoice for underbilled usage unless contract explicitly allows and Finance approves. For most self-serve customers, we will apply prospectively. For enterprise contracts with explicit audit/true-up clauses, case-by-case with Legal.

Notes:
- These thresholds are operational guardrails, not GAAP materiality.
- Any customer-level exception (refund >$10k, or any retroactive invoice) requires CFO approval + Legal review.

2) Financial statement / audit materiality (company-level)
- RevRec will monitor cumulative impact of meter corrections on recognized revenue and deferred revenue.
- Escalate to CFO + external auditors if:
  - Cumulative corrections in a quarter exceed the greater of $250k or 0.5% of quarterly revenue, OR
  - Any single customer correction exceeds $100k, OR
  - Pattern indicates systemic control deficiency (e.g., duplicate rates, late event spikes) that could imply broader misstatement risk.

Retroactive adjustments policy (official posture)
Principles
- Prefer forward-looking fixes with “shadow billing compare” run to quantify impact before invoices are affected.
- Avoid restating prior invoices unless (a) customer was overbilled in a material way, (b) the contract or law requires a refund/credit, or (c) dispute/relationship risk demands remediation.
- Underbilling true-ups are generally avoided unless contractually supported and relationship considerations justify.

Mechanics
- Overbilling remediation method:
  1) Credit memo applied to next invoice (default)
  2) Refund only if customer demands and contract requires, or if customer is churned and cannot realize credit
- Documentation required for each remediation:
  - Root cause category (retry double count / embeddings batch / GPU idle accounting / timezone/export issue / dimension misattribution)
  - Impact calculation (period, meter definition version, query snapshot)
  - Approval chain (Finance + Legal if required)
  - Customer communication artifact (support ticket/email)

- Retroactive invoice re-issuance:
  - Only with CFO + GC approval
  - Must preserve original invoice and issue adjustment per standard AR process

Interaction with pricing experiments
Because meter changes overlap with pricing/packaging experiments, we will segment analyses and ensure RevRec treatment does not contaminate experiment conclusions.
- Required: tag usage/events with meter_version and experiment cohort where available.
- Analyses should be split into:
  - Pre-meter-fix period
  - Shadow period (both meters computed; invoices still on old meter)
  - Post-cutover period

Control requirements (what auditors will ask for)
1) Change control
- Every meter-affecting change must have:
  - Ticket (Linear/Jira) with description of issue, expected impact, and roll-out plan
  - Code review (GitHub PR) linked in the ticket
  - Shadow-billing comparison plan + defined acceptance criteria
  - Finance sign-off prior to cutover if the change can impact billed amounts
  - Backout plan and comms plan if drift is detected

2) Evidence of completeness & accuracy
- Daily/weekly drift checks: usage events vs billing ledger totals (by account/model/region)
- Duplicate rate / idempotency signals tracked
- Data freshness SLO for billing tables (late arriving events policy)

3) Audit trail retention (minimum)
Retain for 7 years (or longer if required by enterprise contract / litigation hold):
- Meter specification versions (Confluence) and effective dates
- Source code PRs and deployment records for meter changes
- Shadow-billing outputs (aggregated deltas by account/day)
- Reconciliation exports used for month-end close
- Approval logs (Finance/Legal sign-offs) and incident reviews for any regression

Specific meter-change impact notes (Feb 2026)
A) Retry/fallback double-counting (Hosted API)
- Customer impact: overbilling risk; dispute likelihood high (customers can reproduce by retrying/timeouts).
- RevRec posture: treat as billing error correction (Class A).
- Remediation: focus on customers with disputes + high usage; credit when Tier 1 threshold met.
- Evidence: link PR, pre/post query showing duplicate token attribution eliminated.

B) Embeddings batch accounting
- Customer impact: could be over or under depending on prior logic.
- RevRec posture: Class A.
- Remediation: if systematic overbill, consider broader credit campaign for affected cohort rather than one-off support credits.

C) Cache-hit attribution / billed_tokens vs raw_tokens
- Customer impact: generally transparency; may change how customers reconcile.
- RevRec posture: not necessarily a billing impact; but can change customer perception and dispute rates.
- Action: ensure contractual language and FAQ define billed_tokens clearly and consistently.

D) Dedicated GPU-seconds v2
- Customer impact: overage/usage components could shift.
- RevRec posture: pending Legal + Product confirmation of current order form language for “GPU-seconds” and idle time. If prior implementation deviated from documented definition, treat as Class A; otherwise Class B and apply prospectively with notice.

Approvals & RACI (for meter changes affecting billing)
- Engineering owner (Telemetry): Logan Wright / Nadia Rahman
- Finance owner (RevRec): Emily Stone
- Finance exec sponsor: Laura Bennett
- Legal owner: Michael Grant (usage definition), Sofia Mendes (commercial contracting)
- Data exports/recon tables: Mei Lin
- Shadow billing rollout/release: Connor O’Brien

Approval requirements
- Any change expected to move billed totals by >0.5% for any top-20 account OR >$50k/month in aggregate: Finance sign-off required before cutover.
- Any change that modifies the meaning of “usage unit” in customer terms (tokens definition, retries policy, cache billing, idle GPU): Legal sign-off required before cutover.

Open questions (need answers before “final”)
1) Contracts: do we have any customers with explicit language that bills server retries / internal fallback tokens? (Legal to sample enterprise MSAs/Order Forms.)
2) Dedicated: does any Order Form define usage as “allocated GPU time” (includes idle) vs “active compute”? This determines Class A vs B for GPU-seconds v2.
3) Customer communications: should we proactively notify customers about billed_tokens vs raw_tokens and cache-hit attribution, or only update docs? (GTM + Legal.)
4) Credit policy consistency: if we decide to credit a cohort for a systemic issue, how do we ensure equal treatment across similarly situated customers to reduce fairness disputes?

Decisions needed
- Confirm operational thresholds (Tier 1/2/3) and whether Support can auto-issue credits up to a cap without CFO review.
- Confirm whether underbilling true-ups are ever allowed for enterprise (and required contract language).
- Confirm retention period and where shadow billing artifacts will live (Snowflake table + Drive export + Confluence page).

Appendix: quick decision tree (summary)
1) Does the meter change affect billed totals?
- No -> document + proceed (still require spec update).
- Yes -> continue.

2) Is the change a correction of an error vs a definition change?
- Correction (Class A) -> run shadow billing; assess remediation for historical overbilling based on thresholds.
- Definition change (Class B/C) -> Legal review; typically prospective; consider notice/Order Form update.

3) Is the historical impact material?
- Customer-level material -> credit/refund path.
- Company-level material -> CFO + auditor escalation.

Document owner notes (Emily)
- This doc will be updated after (a) Legal contract sampling, (b) first full week of shadow billing compare results, and (c) month-end close reconciliation confirms no unresolved drift. Target: move status to “final” by end of Feb close + sign-off from Laura (CFO) and Michael (GC).
