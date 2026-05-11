# Usage Meter Audit 2026 02 Working Notes

Source type: google_drive
Document ID: dsid_dc8da53aae4c4a81be13ae8e519cd42d
Release: v1.0.0
Benchmark: EnterpriseRAG-Bench synthetic upstream data
Usage meter audit — Feb 2026 (working notes)

Owner: Emily Stone (RevRec/Accounting)
Primary Eng contacts: Logan Wright / Nadia Rahman / Mei Lin
Legal: Michael Grant / Sofia Mendes
FP&A: Rishi Malhotra
Last working session: 2026-02-26 (shadow billing week 1 review)

Purpose
- Collect raw audit notes for Feb 2026 meter validation + reconciliation.
- Track suspected discrepancies (invoice vs ledger vs usage events vs customer usage API/export).
- Capture open questions for meter spec + legal definitions.
- Log links to queries/dashboards/threads and decisions.

Scope (current audit)
A) Hosted API (OpenAI-compatible + native)
- tokens: prompt vs completion vs billed_tokens
- requests: what counts as a billable request (streaming starts/ends, cancellations, timeouts)
- embeddings: batched input accounting
- retries/fallback routing/idempotency: prevent double-counting
- cache-hit attribution: billed tokens vs raw tokens and how we show to customers
- dimensions: account_id, workspace/org, model, region, endpoint, plan/experiment cohort

B) Dedicated
- GPU-seconds (v2 in progress): idle accounting, autoscaling transitions, burst rules

Key working assumption (needs explicit sign-off)
- “Invoice is computed from billing ledger, which should be fully reproducible from usage events + meter logic + price book as-of invoice period.”
- For any meter change: run shadow billing compare (old vs new) before impacting invoices.
- Prefer forward fixes; retroactive changes only with Finance + Legal approval and audit trail.


Current risk summary (as of 2026-02-27)
- Highest invoice dispute risk: retries/fallback double-counting on streaming paths (ENG-4430 / PR-27463), and embeddings batched inputs (PR-27569).
- Highest analysis integrity risk: missing region dimension (PR-27541) + missing cache_hit fields (ENG-4412) causing incorrect margin attribution and cohort comparisons.
- Operational risk: late-arriving usage events beyond close freeze window; needs consistent policy + tooling.


Links / where things live
Dashboards
- Meter sanity dashboard spec: confluence:eng-platform/dashboards-and-alerts/meter-sanity-dashboard-spec
- Grafana panels (meter drift/freshness/duplicates): github:observability-pack/pr-1834-add-meter-drift-grafana-dashboard
- Console dashboard (WIP): ENG-4415 / PR-27526

Exports / tables
- Reconciliation export pipeline/table: PR-27510 (Finance access gated)
- Snowflake export request: ENG-4438

Threads (good context)
- Acme discrepancy triage: slack:finance/1770043123-usage-recon-discrepancy-acme
- Close freeze window agreement: slack:finance/1770061189-month-end-close-usage-freeze-window
- Shadow billing compare week 1 results: slack:finance/1770075522-shadow-billing-compare-results-week-1
- Missing region dimension: slack:eng-platform/1770069017-missing-region-dimension-in-usage-events
- Usage event idempotency design: slack:eng-platform/1770094420-usage-event-idempotency-design

Reference docs
- Hosted API meter spec (draft target): confluence:finance-and-legal/revrec-and-billing/billing-meter-spec-hosted-api-tokens-and-requests
- Reconciliation playbook (draft target): confluence:finance-and-legal/revrec-and-billing/metering-to-invoice-reconciliation-playbook
- Month-end checklist (draft target): confluence:finance-and-legal/revrec-and-billing/month-end-close-usage-reconciliation-checklist
- Legal usage terms playbook (draft target): confluence:contracts-and-legal-process/usage-based-pricing-legal-terms-playbook


Working definitions (NOT FINAL — capture deltas)
Tokens
- raw_prompt_tokens: tokens in request payload (after server normalization)
- raw_completion_tokens: tokens generated (including streamed chunks)
- billed_prompt_tokens: prompt tokens chargeable after cache/discount rules
- billed_completion_tokens: completion tokens chargeable after retry/fallback rules
- billed_tokens = billed_prompt_tokens + billed_completion_tokens

Requests
- billable_request_count: count of distinct billable “attempt groups” (idempotent group), not raw HTTP attempts
- streaming requests: “request” counted once if server begins generation, even if client disconnects (needs customer-facing copy)

Cache hit
- cache_hit: boolean or enum (none/partial/full) — needs consistent semantics
- attribution question: if prefix cached, billed_prompt_tokens reduced; raw_prompt_tokens still reported for transparency (confirm how usage API shows)

Retries/fallback
- server-side retry/fallback should not increase billed tokens beyond final successful attempt (unless contract says otherwise; Legal prefers explicit definition)
- client retry with new idempotency key is a new billable group; with same idempotency key should dedupe

Dedicated
- gpu_seconds: sum over active GPUs assigned to pool * wall-clock seconds, minus explicit “unassigned” windows (v2 aims to clarify)


What we’re validating (audit plan notes)
1) Meter logic matches invoice outcomes
- For sampled invoices (see drive spreadsheet), reproduce totals from usage events.
- Slice by account/day/model/region/endpoint.

2) Data completeness + freshness SLOs
- Freshness: how long after request until usage event is available in recon export and customer usage API.
- Late event policy: how handled after close freeze window.

3) Consistency across endpoints
- OpenAI-compatible endpoints vs native endpoints should align on token counting and billed vs raw token fields.

4) Experiment contamination control
- Meter fixes overlap with pricing experiments; analysis must segment pre/post meter change windows.


Open questions (needs answers / owners)
[Q1] Token counting alignment (OpenAI-compatible)
- Are we using the exact same tokenizer + normalization rules across /v1/chat/completions and native /generate?
- Do tool call tokens count as completion tokens? How are they displayed?
Owner: Nadia Rahman
Artifacts: github:redwood-openai-compat/pr-511-align-token-counting-with-openai-compatible-endpoints, PR-27471

[Q2] Streaming partials + disconnects
- If client disconnects mid-stream, do we bill generated tokens up to disconnect? Do we bill any minimum?
- Are partial chunks counted consistently between runtime logs and metering events?
Owner: Logan Wright
Artifacts: ENG-4410 (streaming tests), perf-canary pr-912 scenarios

[Q3] Idempotency definition + dedupe window
- Dedupe key: request_id? idempotency_key + account + endpoint? (risk: collisions vs missed dedupe)
- Dedupe window duration: 24h? 7d? (backfills may create duplicates outside window)
Owner: Cole Summers
Artifacts: PR-27555 (hardening idempotency)

[Q4] Cache-hit fields missing in some events
- Are cache_hit and billed_prompt_tokens nullable today? If so, what default behavior is invoice using?
- Can we safely backfill for Jan/Feb for experiment analysis without changing invoiced totals?
Owner: Mei Lin
Artifacts: ENG-4412 (backfill), PR-27485 (usage API fields)

[Q5] Region dimension correctness
- What is the source for region dimension (edge POP vs compute region vs billing region)?
- For multi-region fallback, do we attribute to final serving region or initial region?
Owner: Logan Wright / Ethan Park
Artifacts: PR-27541

[Q6] Late events + freeze window policy (RevRec)
- Confirm: freeze at T+3 days for month-end close? (thread says tentative)
- If late events arrive after freeze, do they roll into next month revenue? Do we credit/adjust?
Owner: Emily Stone + Laura Bennett
Artifacts: slack:finance/1770061189-month-end-close-usage-freeze-window, revrec impact assessment doc

[Q7] Dedicated GPU-seconds v2
- Definition of idle: if GPU allocated but no requests, do we bill as reserved capacity or usage?
- How do we map gpu_seconds to contract constructs (commit, burst, minimums)?
Owner: Rishi Malhotra + Nadia Rahman
Artifacts: ENG-4426 / PR-27492


Suspected discrepancies (running list)
Format: ID | Account | Product | Period | Symptom | Hypothesis | Severity | Owner | Status/next

D-01 | Acme AI | Hosted API (streaming) | 2026-01-28 to 2026-02-02 | Invoice tokens > usage API tokens by ~3–6% on high-streaming days | double-count on server retry/fallback for streaming; usage API may exclude a class of attempts | High (customer dispute active) | Logan / Nadia | In progress; validate against PR-27463; compare shadow billing outputs
Refs: slack:finance/1770043123-usage-recon-discrepancy-acme, jira:SUP-28451, jira:SUP-28463

D-02 | ZenChat | Hosted API | 2026-02-05 to 2026-02-10 | Usage export shows spikes day boundary; customer claims “double day” | timezone misalignment in daily export aggregation; invoice uses UTC but export uses account tz? | Medium | Mei Lin | Repro in SUP-28522; define canonical TZ; update export doc

D-03 | Northwind Labs (POC) | Hosted API (cache heavy) | 2026-02-12 to 2026-02-18 | Customer sees billed tokens not decreasing despite cache hits in app | cache_hit fields missing/null → billed_prompt_tokens not reduced; or cache only in runtime not wired to meter yet | Medium | Nadia / Mei | Blocked on ENG-4412 backfill + PR-27485 field exposure; confirm pricing policy re cache

D-04 | Contoso Financial | Hosted API | 2026-02-01 to 2026-02-20 | Region-based price tier dispute (EU vs US) | missing/incorrect region dimension; fallback routing attributed to wrong region | High (contract negotiation) | Ethan / Logan | Fix in PR-27541; need customer explanation + contract language option

D-05 | Fabrikam | Dedicated | 2026-01-01 to 2026-01-31 | Dedicated GPU-hours appear overstated vs their internal cluster logs | gpu_seconds v1 counts autoscaling idle + warm pools; mismatch with contract expectation | High | Nadia / Rishi | Waiting for v2 definition; run shadow compare for Jan; legal alignment on definition
Refs: jira:SUP-28505

D-06 | Multiple self-serve | Embeddings | 2026-02-08 to 2026-02-19 | Embeddings billed units inconsistent with number of inputs for batched calls | embeddings batch accounting bug (count per request not per input) | High (systemic) | Logan / Cole | Fix merged in PR-27569; need decision: backfill? forward only?

D-07 | Multiple | Hosted API | Ongoing | Ledger totals drift from usage events totals by 0.5–1.5% in specific region/model combos | late-arriving events; duplicates due to partial failures/backfills; missing dimensions cause rows dropped in aggregation | Medium | Aisha / Mei | Implement drift alerts (ENG-4421), refine thresholds (slack:eng-sre/1770101888-meter-drift-alert-tuning)

D-08 | Sales-led accounts | Hosted API | Feb 2026 | Credits not applied on invoice preview for some accounts | credit application timing vs invoice generation job; ledger join issue on credit effective date | Medium | Emily / Cole | Triage via support thread; confirm policy and add pre-send checklist item
Refs: jira:SUP-28490, slack:support/1770127744-credits-application-incorrect-on-invoice


Shadow billing compare (notes)
Goal
- Run old meter vs new meter on same underlying request logs and compute diff by account/day/model/region.
- Ensure changes are expected (e.g., fixing double-counting) and quantify invoice impact before rollout.

Status (week 1 summary — from slack thread)
- Largest deltas concentrated in streaming + retry-heavy workloads.
- Embeddings batched fix creates visible downshift in billed units for affected endpoints (expected).
- Some “unexpected” upshifts tied to region dimension fill-in (previously dropped rows now counted).

Action items
- Aisha: add dashboard annotation when “dimension fill” fixes cause apparent billing increase (so Finance understands expected behavior).
- Emily: list accounts needing proactive comms if billed totals change materially post-fix (coordinate with Support/Sales).


Daily sanity checks we want (Finance POV)
(These are what we keep asking for; Eng is building dashboard/job + alerts)
- Drift: |sum(usage_events billed_tokens) - sum(billing_ledger billed_tokens)| / ledger <= threshold
- Duplicate usage events rate (by dedupe key)
- Null rate for required dimensions: account_id, model, region, endpoint, request_id/idempotency_key
- Freshness: P95 event available in recon export within X minutes; P99 within Y hours
- “Dropped rows” count in aggregation pipeline (rows failing schema validation)

Draft thresholds (proposed — needs PM-1842 sign-off)
- Drift paging: >1.0% for any top-20 account daily OR >0.3% platform-wide daily
- Duplicate rate paging: >0.05% daily
- Freshness paging: P99 > 6 hours
- Null required dims paging: >0.1% for any required dim


Month-end close policy notes (WIP)
Freeze window proposal
- Close month M at M+3 calendar days 23:59 UTC for usage event ingestion into ledger.
- Late events after freeze: record as “late adjustments” and post to next month unless material + approved for adjustment.

Need to document
- Evidence retained: snapshot of ledger totals, reconciliation export query outputs, variance explanations, approvals.
- Audit trail for meter changes: PR link + shadow billing diff report + Finance sign-off + effective date.


Customer-facing / legal language questions (capturing for Legal)
- Token definition: which tokenizer/version; what counts as prompt vs completion; tool call tokens.
- Retries: “We do not charge additional tokens for server-side retries/fallback” (if true) vs “we charge for tokens generated” (if partials count).
- Caching: how prefix cache reduces billed prompt tokens; whether cache-hit depends on exact match; transparency in reporting.
- Dispute window: timeframe and required evidence (usage export as source? invoice ledger as source?)
- Audit logs: ability to provide per-request usage records under NDA?


Notes from specific meetings (raw)
2026-02-05 (Finance + Eng) — discrepancy sampling
- We should sample at least 15 invoices: 5 self-serve, 5 sales-led, 5 dedicated.
- Priority: accounts with streaming >50% and high retry rate.
- Mei: recon export table will include billed_tokens vs raw_tokens; goal by mid-Feb.

2026-02-11 (Legal) — contract redlines pattern
- Customers pushing hard on “token counting fairness” and “no double charge for retries.”
- Michael: prefer explicit definitions + examples; avoid promising perfect alignment with third-party tokenizers if we can’t guarantee.

2026-02-19 (FP&A) — margin model inputs
- Rishi needs: region attribution correctness + cache-hit rate by model + effective batching factor.
- If region dimension was missing, margin model is wrong; we need to back-cast with corrected region if possible.


Next steps (as of 2026-02-27)
- Emily: finalize discrepancy list v1 and map each to (a) fix PR/ticket, (b) backfill decision, (c) customer comms need.
- Logan/Nadia: publish Hosted API meter spec draft in Confluence; include edge cases + examples.
- Mei/Aisha: stand up daily drift + freshness checks with alerting; confirm thresholds and owners.
- Michael/Sofia: provide recommended contract clause language options for retries + caching + dispute mechanics.
- Rishi: update materiality thresholds for whether retroactive adjustments are required/allowed (tie to revrec impact assessment).


Notes to self (Emily)
- Keep a clear separation: (1) display/usage API semantics vs (2) invoicing ledger semantics. Several disputes are “reporting mismatch,” not true billing error.
- Every fix that changes totals needs: effective date + shadow compare evidence + sign-off (PM-1872).
- For experiments: insist on pre/post segmentation when meter changes overlap exposure windows (Product keeps forgetting).
