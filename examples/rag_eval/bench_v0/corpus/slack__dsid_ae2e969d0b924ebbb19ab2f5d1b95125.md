# 1771741044 Beta Customer Feedback Estimates Seem Off

Source type: slack
Document ID: dsid_ae2e969d0b924ebbb19ab2f5d1b95125
Release: v1.0.0
Benchmark: EnterpriseRAG-Bench synthetic upstream data
support

Dev Patel: Heads up — early beta customer feedback: savings estimates in Suggestions look inflated vs what they see in billing. This is coming in via SUP-48271. Linking ticket: sources/jira/customer-support/SUP-48271-suggestions-savings-estimate-does-not-match-billing.json

Miguel Santos: yup, this is Arkadia Finance (beta). They said Console shows “~$1.2k/day (18%)” for a caching suggestion on their `/chat/completions` route, but their month-to-date costs don’t line up. They’re worried we’re “making up numbers”.

Supportbot: Ticket SUP-48271 updated by Miguel Santos (status: Investigating)

Marissa Cole: can someone paste the exact UI values (savings %, $/day, risk, confidence) + route/model? Want to see if this is a pricing assumption thing vs telemetry mismatch.

Miguel Santos: from ticket:
- Org: arkadia-finance
- Project: prod-vpc
- Route: `chat-prod` (their named route)
- Model: `rw-llama-3.1-70b-instruct`
- Suggestion type: “Enable prefix cache”
- Estimated savings: `$1,214/day` (~18%)
- Latency impact: p50 +6ms, p95 +22ms
- Risk: Low
- Confidence: Medium
They also mentioned they already have some app-side caching.

Aditya Rao: thanks. first guess: estimator is using list price per token (hosted API) but Arkadia is on dedicated w/ committed rate card + blended GPU cost attribution. If we’re multiplying tokens by default price, we’ll overstate $.

Nadia Rahman: +1. Billing for Dedicated isn’t “per token list price”; it’s capacity + overage, and the cost attribution pipeline allocates by utilization. Suggestions estimator v0 currently uses `effective_token_price_usd` only when we have it, otherwise fallback to catalog price. Arkadia might be missing the effective price feed.

Logan Wright: checking telemetry: do we have the `cost_attribution.v2` aggregates for that org? If they’re on VPC/dedicated, sometimes the daily aggregates lag or are disabled depending on region.

Chloe Martin: I can look in optimize-suggestions service logs. Do we have the suggestion_id from the UI? (stable id should be in the drawer -> “Details” -> copy link)

Miguel Santos: from their screenshot URL in ticket (cropped, sorry) I can see `suggestion_id=opt_sug_01J3R8H9M7Y2KQ4...` (truncated). I asked them to copy the “Share suggestion” link.

Dylan Brooks: another angle: are we comparing against billing *after* discounts/credits? UI is showing “estimated compute savings” before discounts. Copy currently says “estimated savings” but doesn’t call out discounts/credits. Might be a perception gap.

Sana Farid: also, “already have some app-side caching” matters. Our baseline assumes Redwood cache hit rate is current observed `prefix_cache_hit_rate` (likely low if they’re not using Redwood cache). If they’re caching upstream, their request mix hitting Redwood may be different than what they think (e.g., they’re caching prompts but still sending lots of unique prompts to Redwood). Could go either way.

Dr. Maya Srinivasan: re: inflation — we’ve seen a bug in dogfood where we used *prompt tokens* for savings but billing uses *billed tokens* after prompt compression + retries accounted differently. If Arkadia has retries/timeouts, request-level token counts can be noisy.

Aditya Rao: action items:
1) confirm whether estimator used fallback price vs effective dedicated price.
2) confirm token basis used (input/output, before/after cache, etc.)
3) confirm time window alignment (UI might be last 24h traffic, billing is month-to-date)

Marissa Cole: + please don’t leave support hanging — we need a crisp explanation + next step while we debug.

Dev Patel: agreed. For ticket response: can we say “Suggestions uses last 7 days traffic + current metered token rates; billing may include discounts/credits and can be on a different pricing basis for Dedicated” + ask for time range + confirm dedicated plan? I’ll draft but want sign-off.

Nadia Rahman: yes, but be specific: “estimates are directional and based on observed request volume + token counts in telemetry; for Dedicated, $ estimates may not reflect your contracted effective rate card yet.” Also ask if they’re looking at invoiced amount vs internal allocation dashboard.

Logan Wright: I pulled quick stats: arkadia-finance `cost_attribution.v2` looks partially missing last 3 days in `us-east-1` (gap starting ~02:00 UTC). If estimator is backfilling with catalog price during gap, that could bump $.

Chloe Martin: found it. In optimize-suggestions logs for arkadia-finance, price source = `catalog_fallback` for multiple suggestion computations:
`price_source=catalog_fallback reason=missing_effective_price org=arkadia-finance project=prod-vpc model=rw-llama-3.1-70b-instruct`
So yeah, we’re using list price.

Aditya Rao: ok that explains the $ inflation. % savings might still be directionally ok, but dollars are wrong for them.

Dylan Brooks: can we hotfix UI to show “% savings” as primary and dollars as secondary w/ tooltip: “Dollar estimates use list price when effective rate not available”? Might help reduce immediate trust hit.

Marissa Cole: we should do that, but for this thread: what’s the near-term mitigation? Can we suppress $ when price_source=catalog_fallback for Dedicated/Private?

Chloe Martin: backend can add a field like `savings_usd_is_estimated_from_list_price=true` (or reuse existing `assumptions`) but that’s a PR. For now, support response + maybe manually recompute using their effective rate if Nadia can provide it.

Nadia Rahman: I don’t have their contract numbers here, but we can approximate using their internal cost attribution dashboard if it’s working historically. Once Logan fixes the missing effective price feed, estimator will recompute.

Logan Wright: I can open an incident-ish task to backfill the `effective_token_price_usd` aggregates for arkadia-finance + check why the job stopped. Might be the new ingestion pipeline change last week.

Sana Farid: also worth checking if confidence should have been “Low” when price_source is fallback. Right now confidence is based on traffic/latency variance, not price source. We should degrade confidence when core assumption is missing.

Dr. Maya Srinivasan: +1. If dollars are wrong, confidence shouldn’t read “Medium” even if traffic is stable.

Dev Patel: for SUP-48271 reply draft (tell me if ok):
“Thanks — the Suggestions beta shows *estimated* savings based on recent request telemetry. In some deployments (incl. Dedicated/Private), the dollar value may temporarily use a list-price assumption when the effective rate card feed isn’t available, which can make the $/day estimate appear higher than your invoiced amount (which may include contracted rates, discounts, credits). The % savings and latency impact are still computed from observed traffic, but we agree the dollar presentation can be confusing. We’re investigating and will update your org’s estimates once the effective pricing feed is restored. In the meantime, if you share the time window you’re comparing and whether you’re looking at invoice totals vs allocation, we can reconcile the numbers with you.”

Marissa Cole: looks good. add one sentence: “UI is typically based on last 7 days / last 24h (depending on view)” so they align the window.

Miguel Santos: I’ll send that, and ask them for the “Share suggestion” link + confirm if they’re using upstream caching. Also offering a quick call.

Chloe Martin: engineering notes for whoever picks up: we should 1) bubble `price_source` into response; 2) if `catalog_fallback` and deployment_mode != hosted, either hide $ or flag it prominently.

Aditya Rao: I’ll file an Optimize follow-up. Also, we should trigger a recompute once Logan backfills the effective price.

Logan Wright: on it. I’ll backfill and ping when complete. ETA ~2 hours if the pipeline is healthy.

Dev Patel: thanks all. Please keep updates in SUP-48271 so Support has a single source. I’ll mark this thread as tracking + will update the beta FAQ copy once we have the fix.

Supportbot: Ticket SUP-48271 internal note added by Dev Patel
