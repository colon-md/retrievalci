# Pr 27463 Fix Double Counting On Stream Retry

Source type: github
Document ID: dsid_38e7aac0d2ed4545b7a92c2c441825a1
Release: v1.0.0
Benchmark: EnterpriseRAG-Bench synthetic upstream data
Fix double-counting on stream retry/fallback for Hosted API metering

Context: We’ve seen invoice disputes and internal drift checks flag double-counted billed_tokens when the server performs a retry/fallback during streaming responses (e.g., upstream timeout mid-stream, router switches to a compatible model/region, or control plane triggers a hedged request). The customer sees one response stream, but we were emitting usage events for both the initial attempt and the retried attempt, and the ledger aggregation was summing both. This PR fixes the retry/fallback accounting path for streaming so that billed token meters reflect the final successful attempt only.

Root cause: In streaming mode, we incrementally populated a per-attempt UsageAccumulator and emitted a usage_event on stream termination for each attempt (including attempts that were superseded). When a retry/fallback occurred after partial chunks, the original attempt emitted a terminal usage_event with partial token counts; the retried attempt emitted its own terminal usage_event; downstream billing ledger summed both because both had unique event_ids and the aggregator did not have a stable “superseded by” key.

Fix (high level): (1) Introduce a stable request-scoped usage_dedupe_key derived from (account_id, api_key_id, idempotency_key||request_id, billing_period_day, meter_version). (2) Emit attempt-scoped usage events with attempt_seq and a new terminal_reason enum. (3) Update ledger aggregation to bill only the max attempt_seq per usage_dedupe_key where terminal_reason == SUCCESS, and to ignore attempts where terminal_reason in {SUPERSEDED, RETRY_ABORTED}. (4) For the streaming handler, ensure we mark the prior attempt as SUPERSEDED before starting the retry path, and we do not emit a SUCCESS terminal event for any attempt that did not complete.

Details:
- New fields on usage events:
  - usage_dedupe_key: stable across retries/fallbacks for the logical request
  - attempt_seq: monotonic int starting at 0
  - terminal_reason: SUCCESS | ERROR | SUPERSEDED | RETRY_ABORTED | CLIENT_DISCONNECT
  - superseded_by_attempt_seq: populated on SUPERSEDED events
- Streaming retry semantics: When router triggers retry/fallback after partial streaming, we immediately close the old accumulator with terminal_reason=SUPERSEDED (not SUCCESS) and start a new accumulator for attempt_seq+1. Only the attempt that reaches stream completion and returns a final usage summary is eligible for billing.
- Metrics/observability: Added counters for retry_double_count_prevented_total and usage_events_superseded_total, plus a gauge for per-route superseded rate to help tune alerting. Added structured log fields for usage_dedupe_key and attempt_seq in the streaming gateway.

Tests:
- Unit tests in metering: simulate (a) stream completes without retry; (b) retry after N chunks; (c) fallback to different model mid-stream; (d) client disconnect then server retry does not run; verify billed tokens count equals final successful attempt only and that superseded attempts are excluded.
- Integration test: added a harness that forces an upstream timeout on first attempt and verifies the exported usage API returns a single billed record for the request (with billed_tokens matching the final attempt).
- Regression: added a fixture for OpenAI-compatible streaming endpoint where retries previously double-counted due to different request_id normalization.

Rollout notes:
- This change is behind feature flag billing.metering_stream_retry_dedupe_v1 (default off). Plan: enable in staging for 48h, then canary 5% of hosted traffic (US-East + two high-volume accounts), then ramp to 50% and 100% if drift checks remain within thresholds.
- Shadow-billing: for the canary phase, ledger will compute both old and new billable usage in parallel (annotated in the recon export) to quantify deltas; Finance/RevRec sign-off required before 100% rollout.
- Backfill: no backfill in this PR. If shadow-billing indicates material historical impact, we’ll follow the RevRec playbook for corrective credits rather than retroactive invoice edits.

Customer impact: Fixes an overbilling bug in specific streaming retry/fallback scenarios (primarily server-side retries). In most cases, customers should see billed token counts decrease slightly for affected days; usage API exports will show clearer attempt attribution fields once the related API surface PR lands.

Checklist:
- [x] Added unit coverage for streaming retry + fallback paths
- [x] Added integration coverage for forced retry in staging
- [x] Added metrics + logs for superseded attempts
- [x] Rollout plan documented + flag added
- [x] Linked to support escalation SUP-28463 and Linear ENG-4430

Notes for reviewers: Please focus on (a) dedupe key stability across OpenAI-compatible endpoints, (b) ensuring we never bill SUPERSEDED attempts, and (c) any edge cases where SUCCESS could be emitted after partial stream termination.
Logan Wright (review): The dedupe_key derivation uses request_id for non-idempotent requests — do we normalize OpenAI-compat request IDs the same way as native endpoints? Author (Cole): Good catch. I added normalization to strip the gateway-generated suffix and included a regression test for /v1/chat/completions streaming. Nadia Rahman (review): Can we guarantee terminal_reason cannot be SUCCESS for the superseded attempt if the client disconnects? Also please document semantics of CLIENT_DISCONNECT vs SUPERSEDED. Author: Updated the state machine so SUCCESS is only emitted from the finalization path that sees a completed stream; client disconnect now emits CLIENT_DISCONNECT and short-circuits retries. Added inline docs and tests. Aisha Bello (review): Please add a metric we can alert on for high superseded rates per route/model; also add log fields so SRE can join events. Author: Added usage_events_superseded_total with route+model labels (bounded), and structured log fields usage_dedupe_key/attempt_seq. Connor O'Brien (review): Rollout plan needs an explicit canary and revert procedure. Author: Expanded rollout section with percentages, regions, and revert steps (flag off, keep shadow-billing for 24h). Monica Patel (review): Ensure SDK retries using idempotency_key don’t change dedupe semantics. Author: Dedupe prefers idempotency_key when present; added a test that simulates client retry with same idempotency key and confirms single billable attempt. Final: Approved after changes; merged squash.
Fixes a bug where streaming retries/fallbacks could double-count billed tokens by ensuring only the final successful attempt is billable; adds retry attempt attribution fields and regression coverage; rolled out behind feature flag billing.metering_stream_retry_dedupe_v1 with shadow-billing support.
