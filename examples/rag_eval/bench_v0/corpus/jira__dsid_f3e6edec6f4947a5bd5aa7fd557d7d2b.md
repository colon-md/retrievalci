# Sup 28488 Billing Question Burst Usage Metering

Source type: jira
Document ID: dsid_f3e6edec6f4947a5bd5aa7fd557d7d2b
Release: v1.0.0
Benchmark: EnterpriseRAG-Bench synthetic upstream data
Billing question: Burst usage line item appears higher than expected for Dedicated tenant (reconciliation request)

Issue Summary
Northpeak Search reached out with questions about the new “Dedicated Burst Usage” line item on their June invoice draft. They believe the burst charges for 2025-06-15 to 2025-06-18 are ~1.6–1.9x higher than expected based on their internal job schedule.

Customer Context
- Customer: Northpeak Search
- Product: Dedicated (Burst enabled)
- Region / hardware: us-east (H100 pool)
- Workload: periodic indexing jobs that intentionally burst for ~30–90 minutes, plus steady baseline serving.

Impact
Billing trust / customer confidence issue. No production outage, but customer requested we pause invoice approval until reconciliation is complete.

What the customer observed
- Invoice draft shows:
  - Dedicated Baseline (as expected)
  - Dedicated Burst Usage (higher than expected)
- Customer believes they ran 3 burst windows; our charges look closer to 5–6 windows.

Initial Hypotheses
1) Retries are being counted as additional burst usage (idempotency / double-counting bug).
2) Console “Burst Usage” view is lagging or using a different aggregation window than billing export.
3) Burst “cooldown / denial” behavior caused extra retries that increased token/GPU-sec usage (but should still be accurately metered).

Requested from customer (to reconcile)
- Approximate timestamps of indexing job bursts (start/end) and request IDs if available.
- Confirmation whether client is retrying on 429 / overload or admission-denied responses, and what retry policy/backoff is configured.

Internal Reconciliation Plan
1) Compare billing export (daily burst usage CSV) vs. Console usage charts for same tenant/time range.
2) Query metering pipeline for tenant_id + request_id aggregation; look for duplicate request_id entries.
3) Cross-check gateway logs for elevated retries and response codes during windows in question.
4) If discrepancy confirmed, coordinate credit/rebill with Finance (Rishi/Laura) and provide a customer-readable explanation.

Current Status
Confirmed discrepancy due to retry-path double-counting for burst metering under specific client retry behavior (see investigation notes). Fix merged and scheduled for backfill + billing adjustment.

Next Steps
- Apply metering fix (already merged) + run backfill job for affected dates.
- Provide reconciled burst usage report to customer with timestamps and methodology.
- Finance to issue invoice adjustment/credit memo as needed.

Owner(s)
- Support / CS: Miguel Santos
- Billing hooks: Cole Summers
- Metering pipeline: Nadia Rahman
- Finance: Rishi Malhotra (analysis), Laura Bennett (approval)

Attachments / Links
- PR: pr-24108-fix-billing-double-counting-on-burst-retries
- Burst usage report export job: pr-24091-add-burst-usage-report-export
- Packaging / metering definition: Dedicated burst option packaging doc
2025-06-19 (Miguel Santos): Customer flagged burst usage line item as higher than expected. They requested reconciliation before approving invoice. Looping in Cole (billing hooks) + Nadia (metering) + Rishi (FP&A).
2025-06-19 (Customer - Northpeak Search): We ran three indexing jobs that should have burst for about ~1 hour each. The invoice draft suggests almost double. Can you confirm what’s included in burst usage and whether retries can inflate it?
2025-06-19 (Cole Summers): Acknowledged. We meter burst usage as incremental usage above baseline capacity allocation (GPU-seconds and token accounting rolled up daily). Retries should not double-count if request IDs are consistent, but we’ll validate in the pipeline. Can you share your retry settings and whether you retry on 429/admission-denied?
2025-06-20 (Miguel Santos): Customer confirms client retries on 429 with exponential backoff; they also had a transient spike in 429s during one of the windows. They can’t easily provide request IDs but provided burst window timestamps: 06/15 ~09:10–10:05 PT, 06/17 ~08:55–09:40 PT, 06/18 ~09:05–10:00 PT.
2025-06-20 (Nadia Rahman): Checked metering aggregation for tenant. Seeing duplicated entries with same upstream correlation key but different internal attempt IDs. Looks consistent with double-counting on retry path when idempotency key not propagated to metering event. Escalating to Cole + Vikram for confirm in gateway->metering event payload.
2025-06-21 (Cole Summers): Confirmed bug: burst metering event increments on each retry attempt when idempotency_key is missing/empty, and the downstream de-dupe is keyed only on (tenant_id, request_id). In some retry cases, request_id changes per attempt (gateway regeneration). Fix in progress: use idempotency_key when present; otherwise derive a stable hash from upstream trace/correlation IDs with safeguards.
2025-06-21 (Rishi Malhotra): From finance side: OK to hold invoice finalization for Northpeak pending a reconciliation report + adjustment. Please provide estimated delta and a customer-facing explanation for approval.
2025-06-22 (Cole Summers): Fix merged: pr-24108-fix-billing-double-counting-on-burst-retries. Includes: (1) idempotency key propagation requirement from gateway, (2) de-dupe improvements, (3) reconciliation check that flags duplicate-attempt spikes. Next: run backfill for 06/15–06/18 and generate corrected usage report export.
2025-06-23 (Miguel Santos): Updated customer that we found a metering bug related to retry attempts and we’re running a backfill to correct the burst usage line item. ETA 24–48 hours for final reconciled numbers and invoice adjustment.
2025-06-24 (Nadia Rahman): Backfill completed for affected dates. Corrected burst usage reduced by 41% for 06/15 and 18% for 06/18; 06/17 unchanged. Exported corrected report to billing bucket and validated against Console usage (now consistent within normal aggregation variance).
2025-06-25 (Laura Bennett): Approved issuing a credit/adjustment for the delta and proceeding with corrected invoice. Please document in the ticket and attach the reconciled report summary for audit trail.
2025-06-26 (Miguel Santos): Sent customer reconciled burst usage summary (daily totals + explanation). Customer confirmed it matches their expectations and approved moving forward. Closing ticket as Resolved.
Root cause was double-counting burst metering on retry attempts when a stable idempotency key was not used; request_id changed per attempt and downstream de-dupe did not catch duplicates. Fix merged (pr-24108), backfill run for affected dates, and Finance approved invoice adjustment/credit for the corrected delta. Provided reconciled burst usage report to customer and clarified retry guidance to avoid unnecessary 429-triggered retries.
Follow-up actions: (1) Add automated detection alert for sudden duplicate-attempt ratio in burst metering events; (2) Update customer docs to recommend idempotency keys and retry-on-denial guidance; (3) Ensure Console and billing exports share the same aggregation window definitions; (4) Post a short note to #support with the symptom pattern and how to spot it quickly in dashboards.
