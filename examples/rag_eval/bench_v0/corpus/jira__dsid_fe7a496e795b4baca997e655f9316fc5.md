# Int 3310 Event Tracking Discrepancy Activation Funnel

Source type: jira
Document ID: dsid_fe7a496e795b4baca997e655f9316fc5
Release: v1.0.0
Benchmark: EnterpriseRAG-Bench synthetic upstream data
Activation funnel dashboard discrepancy (EU-West drop + mismatch vs warehouse) after lifecycle growth instrumentation changes

Issue summary
- Growth/Marketing and Product flagged a sudden drop and cross-source mismatch in the activation funnel metrics (signup → API key created → first request) used for the Lifecycle Growth Experiments sprint.
- Primary symptom: EU-West conversion from signup → api_key_created appeared ~12–18% lower than US-East starting 2026-01-21 18:00 UTC, and overall “new signups” appeared higher than expected vs auth/user tables.
- Impact: Week 1–2 experiment readouts risked being invalid/noisy; we paused one planned expansion of the email A/B until numbers were reconciled.

Impact
- Affected dashboards: Activation Funnel (Observability Pack) + internal Looker view used by Growth.
- Impacted decisions: stop/go thresholds and lift calculations for onboarding email v2 and console nudge experiments.
- Customer impact: none direct (reporting/measurement issue only), but could cause us to make incorrect product/GT-M decisions.

Detection
- Reported via Slack #help thread ("events missing in one region") and follow-up in #eng-platform on 2026-01-22.
- Observability Pack funnel panel showed EU-West drop; Mei Lin noted mismatch vs daily new users in warehouse.

What changed recently
- PR-28431: added/standardized activation events for signup → api key → first request → first 100k tokens.
- PR-241: new dashboard panels + funnel query definitions.
- Console nudge framework shipped behind flags; additional event properties and cohort assignment introduced during the week.

Initial hypotheses
1) Ingestion lag or partial pipeline outage in EU-West.
2) Region routing misconfiguration (EU events landing in US dataset or vice versa).
3) Event validation rejecting records due to missing required properties (user_id, event_id) for EU only.
4) Duplicate event emission inflating baseline in one region, making EU look like it dropped.

Investigation notes (high level)
- Confirmed ingestion health: Kafka consumer lag normal; no sustained drop in raw event volume by region at the edge collectors.
- Cross-checked three sources for the same window (2026-01-21 00:00–2026-01-22 23:59 UTC):
  - Raw collector logs: stable event counts in eu-west.
  - Warehouse fact_events table: stable api_key_created events in eu-west.
  - Activation Funnel dashboard: apparent eu-west drop.
- Found the discrepancy was query-side + dedupe behavior:
  - The funnel panel’s Step 1 “signup” was keyed off signup_created events and deduped by (user_id, day), but the Step 1 event was being emitted twice for a subset of flows in US-East due to a console/auth edge case.
  - EU-West had fewer duplicates, so the “conversion rate” (step2/step1) looked artificially worse in EU-West when comparing regions.
  - Additionally, the dashboard query used a join that unintentionally filtered some EU users when event_time skew occurred near midnight UTC (session boundary + region bucket), amplifying the appearance of a regional issue.

Root cause
- Duplicate emission of signup_created/signup_completed for users who hit both (a) email verification completion and (b) immediate console onboarding flow that retriggered the signup event. This was more common in US-East due to traffic mix and a feature-flag exposure path.
- Dashboard query logic for Step 1 + Step 2 used inconsistent dedupe keys (Step 1 deduped by user_id/day; Step 2 deduped by event_id) and a region/date join condition that excluded a portion of EU events at boundary times.
- Net effect: inflated Step 1 counts in US-East and undercounted Step 2 in EU-West in the visualization layer, while the warehouse raw events were largely correct.

Resolution
- Engineering fix: PR-28521 shipped to stop duplicate signup event emission; verified by before/after comparison on a canary cohort and then full rollout.
- Analytics fix: updated Activation Funnel panel query to use consistent dedupe (event_id preferred, fallback to (user_id, event_name, request_id) for older records) and removed the join condition that filtered EU events at UTC boundaries.
- Backfill: no backfill required for canonical fact tables; dashboard now recomputes accurately from warehouse. Noted that historical conversion rates for 2026-01-19 to 2026-01-22 should be interpreted with caution; we annotated the dashboard.

Validation
- Post-fix checks (2026-01-26):
  - Regional conversion rates are within expected variance (eu-west no longer showing step-change).
  - New user counts align within ~1–2% between auth table and deduped signup events.
  - Funnel step drop-offs align with expected product behavior and match secondary metrics (API key creation table).

Follow-ups
- Add a data-quality alert: spike in duplicate rate for signup_created and api_key_created by region.
- Add unit tests/contract tests for event emission idempotency in console/auth flows.
- Document dedupe keys and “source of truth” definitions in the activation metrics notes + dashboard README.

Stakeholders
- Growth: Ben Carter (experiment owner), Tyler Benson (attribution)
- Product: Marissa Cole (console), Paula Mendes (telemetry/rollouts)
- Eng/Analytics: Logan Wright (assigned), Nadia Rahman, Mei Lin

References
- Slack: #help (tracking discrepancy), #eng-platform (taxonomy + dedupe)
- Related PRs: PR-28431, PR-28521, Observability Pack PR-241
2026-01-22 10:18 UTC — Allison Grant (Reporter): Flagging as INT because this is blocking lifecycle sprint measurement. Dashboard shows eu-west signup→api_key_created down ~15% vs yesterday; warehouse counts don’t match. Linking Slack thread from #help where Growth asked who can debug regional missing events.
2026-01-22 12:05 UTC — Logan Wright (Assignee): Acknowledged. Pulling collector + warehouse comparisons for eu-west vs us-east. Initial glance: no obvious collector ingestion lag; going to compare dedupe rates by region for signup_created.
2026-01-22 14:40 UTC — Mei Lin: In Snowflake, api_key_created counts by region look stable; signup_created seems inflated in us-east starting 1/21 evening. Suspect duplicate emission rather than eu-west loss. Can share query if needed.
2026-01-22 16:10 UTC — Nadia Rahman: I see two signup events per user for a subset with the same user_id but different request_id. Likely from console onboarding + auth verification both firing. Repro attempt ongoing; will coordinate with Dylan/Chloe.
2026-01-23 09:30 UTC — Logan Wright: Confirmed: funnel visualization is using inconsistent dedupe keys across steps and an extra join that drops some eu-west events around UTC day boundary. Short-term mitigation: pause region comparison in readouts; use warehouse deduped table for activation rate until we patch dashboards.
2026-01-23 13:55 UTC — Dylan Brooks: We can patch duplicate event emission quickly. There’s a known console/auth edge case where the signup event fires twice after verification redirect. Will ship fix behind a small canary first.
2026-01-24 11:12 UTC — Logan Wright: PR-28521 opened to fix duplicate signup emission; requesting fast review from platform + console owners. Once merged, we’ll update dashboard query to use event_id-first dedupe and remove the problematic join.
2026-01-25 18:20 UTC — Logan Wright: PR-28521 merged + deployed. Duplicate rate on signup_created dropped from ~7–9% to <0.5% in us-east within 2 hours. Updating Observability Pack funnel query now; will annotate dashboard for the impacted window.
2026-01-26 09:05 UTC — Sergio Costa: Dashboard panels updated; verified against Mei’s warehouse query. EU-West conversion no longer shows the step-change. Added a note on the dashboard for 1/19–1/22 data interpretation.
2026-01-26 09:30 UTC — Allison Grant: Closing. Please add follow-up to create an automated alert on duplicate event rates by region; this should be caught before Growth sees it in readouts.
