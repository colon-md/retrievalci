# Pr 18458 Smart Routing Fallback When Breaker Open

Source type: github
Document ID: dsid_960a2981de674ebf844f7dc19d2e3cc0
Release: v1.0.0
Benchmark: EnterpriseRAG-Bench synthetic upstream data
Smart Routing: fallback model/region when overload breaker is open (bounded + loop-safe)

Context: As part of the hosted overload protection work, we want to degrade gracefully instead of cascading timeouts when a model/region is saturated. This PR adds a routing-side fallback attempt when the upstream admission layer reports an overload breaker open (or equivalent overload classification).

What this PR does: Implements a fallback resolution step in Smart Routing that can (optionally) route to a compatible fallback model and/or fallback region based on tenant + route policy. Adds strict loop prevention so we don’t bounce between model variants/regions, and bounds fallback attempts to a small fixed number to avoid amplifying load. Also ensures we do not fallback for non-overload failures (e.g., auth, invalid params, quota exceeded).

Key behaviors: (1) Trigger conditions: only when overload classification indicates breaker open / load shed / capacity unavailable (see mapping in code comments); not triggered on generic 5xx. (2) Fallback selection: consults policy engine for allowed fallbacks for the tenant + route + requested model; if none configured, behave as before. (3) Bounded attempts: max 1 fallback per request by default (configurable to 2 for region+model hop, but guarded); stops immediately if fallback target equals original target. (4) Loop prevention: propagates an internal routing context header across internal hops and records visited (model, region) pairs; if the candidate is already visited, we do not attempt it. (5) Observability: emits counters for fallback_attempted, fallback_succeeded, fallback_blocked_loop, and fallback_exhausted; dimensions intentionally low-cardinality (tenant_tier, route_group, requested_model_family, result).

Why routing (not gateway): The gateway admission layer is responsible for shedding early, but only routing knows compatibility constraints and tenant policy for fallback targets. This is consistent with the admission layering guidance (see ADR-015) — the gateway classifies overload; routing decides a safe alternate target; runtime remains the source of saturation signals.

Safety constraints: Dedicated and Private tenants are never routed to Hosted. Hosted self-serve can fallback within Hosted only. Enterprise Hosted can fallback only to configured allowlisted model variants and regions. We also explicitly disallow cross-AZ fallback within a region to avoid hiding infra issues; region fallback is an explicit opt-in.

Testing: Unit tests cover loop prevention, bounded retries, policy deny, and correct trigger mapping. Integration test fakes breaker-open signals and verifies we attempt exactly one fallback and preserve the correlation ID. Perf-canary scenario work is tracked separately.

Follow-ups: (a) tune default max attempts and error mapping once we have canary data; (b) add dashboard panel for fallback rates vs shed rate; (c) document customer-facing behavior once GA’d (docs owned by DevEx/Docs).
- Smart Routing can now attempt policy-configured fallback model/region when overload protection opens a circuit breaker, with bounded retries and loop prevention. No API surface changes; behavior is opt-in via policy.
Mina Haddad (review): Can you clarify the max attempts logic? I want to ensure we can’t end up trying multiple fallbacks and increasing traffic under overload. Also, please confirm we never cross Hosted/Dedicated boundaries.

Elena Popov (author): Default is max 1 fallback attempt per request. There’s an internal config to allow 2 only for the (region hop -> model hop) path, but I kept it disabled by default and added a hard cap + test. Added explicit checks: Dedicated/Private never route into Hosted, and Hosted never routes into Dedicated.

Ravi Menon (review): Loop prevention looks good. One concern: you’re using requested_model as a label value in metrics. That can be high cardinality. Please bucket by model family or a normalized name.

Elena Popov (author): Good catch. Updated metrics to use requested_model_family (derived from the catalog) and route_group (chat/embeddings/rerank), and removed raw model IDs. Added a metrics test to enforce label set.

Peter Holtz (embedded SRE, review): What’s the trigger mapping? We should only fallback on overload-derived classifications, not generic 503s from downstream. Also ensure correlation ID is preserved for support.

Elena Popov (author): Added overload_mapping.ts and tests. We only fallback on explicit overload reasons (breaker_open, shed, capacity_unavailable) coming from gateway/runtime classification. Generic downstream 503/500 doesn’t trigger fallback. Correlation ID is preserved; added integration test asserting x-redwood-correlation-id is unchanged.

Rafael Mendes (SRE lead, review): Please add a guard against fallback storms: if the fallback target is also overloaded, we should not keep trying. Make sure we return the overload error and include a stable internal reason for dashboards.

Elena Popov (author): Implemented: if fallback attempt returns overload again, we stop (bounded) and return the overload classification. We emit fallback_exhausted with result=overload and include internal reason in routing decision logs (not as a metric label).

Jordan Lee (policy engine, review): Policy selection should be tenant-aware and route-aware. Ensure we validate that the fallback target is in the allowlist; no implicit compatibility.

Elena Popov (author): Done. Fallback targets must be explicitly configured via policy for the tenant + route selector; we also require compatibility check against catalog (same modality + context window constraints). No implicit model swaps.

Mina Haddad (review): Approved after last changes.

Ravi Menon (review): Approved.

Peter Holtz (review): Approved.

Rafael Mendes (review): Approved; please coordinate canary enablement with SRE runbook.

Elena Popov (author): Will do. Canary enablement tracked under ENG-4821 checklist.
