# 20260204 Eu Data Residency Questions And Approved Wording

Source type: gmail
Document ID: dsid_6fc6a90cd2d044e5add05493803e64d1
Release: v1.0.0
Benchmark: EnterpriseRAG-Bench synthetic upstream data
Re: Nordbank – EU data residency questions (approved wording for questionnaire)

From: Svenja Keller <svenja.keller@nordbank.example>
To: Hannah Schmitt <hannah.schmitt@redwoodinference.com>
Cc: Tomasz Nowak <tomasz.nowak@nordbank.example>
Date: Wed, Feb 4, 2026 at 08:17 AM +0100
Subject: Nordbank – EU data residency questions (for questionnaire)

Hi Hannah,

As part of our security questionnaire (v3.2 attached), we need a written statement on data residency.

Could you please confirm, for your hosted service when configured to EU:

1) Where are prompts/inputs and model outputs processed (EU vs US)?
2) Are prompts/outputs stored at rest by default? If yes, for how long and where?
3) Where are logs stored (application logs, audit logs, access logs)?
4) Is any data replicated cross-region for DR/backups?
5) Can support staff outside the EU access our data (even transiently), and under what controls?
6) For “Dedicated” vs “Private” deployments, is the answer different?

We need wording suitable for direct paste into the questionnaire.

Thanks,
Svenja

Svenja Keller
Vendor Risk Management | Nordbank

Attachment: Nordbank_Security_Questionnaire_v3.2.xlsx

---

From: Hannah Schmitt <hannah.schmitt@redwoodinference.com>
To: Olga Petrov <olga.petrov@redwoodinference.com>
Cc: Lukas Meyer <lukas.meyer@redwoodinference.com>, Marcus Lin <marcus.lin@redwoodinference.com>, Daniel Carter <daniel.carter@redwoodinference.com>
Date: Wed, Feb 4, 2026 at 09:03 AM +0100
Subject: Fwd: Nordbank – EU data residency questions (for questionnaire)

Hi Olga + team,

Forwarding Nordbank’s residency questions below. They want paste-ready language for the questionnaire.

They’re evaluating Dedicated + potentially Private in a later phase; near-term is Hosted in EU.

Can you please provide:
- approved wording blocks for Hosted (EU region), Dedicated (EU region), and Private (VPC/on-prem),
- any specific caveats we MUST include (support access, DR replication, log residency),
- and if we should attach the residency/retention one-pager draft.

Their RFP cadence is tight; would love to send today.

Thanks,
Hannah

Hannah Schmitt
Account Executive, EMEA | Redwood Inference

---

From: Lukas Meyer <lukas.meyer@redwoodinference.com>
To: Hannah Schmitt <hannah.schmitt@redwoodinference.com>
Cc: Olga Petrov <olga.petrov@redwoodinference.com>, Marcus Lin <marcus.lin@redwoodinference.com>, Daniel Carter <daniel.carter@redwoodinference.com>
Date: Wed, Feb 4, 2026 at 09:19 AM +0100
Subject: Re: Fwd: Nordbank – EU data residency questions (for questionnaire)

+1. Please make sure we’re explicit that “EU region” refers to processing/storage in the selected EU region, and not a blanket “EU-only” commitment across all systems.

We’ve been getting caught by questionnaires that interpret residency as “no non-EU personnel can ever access anything.” We should use the standard “limited, authorized access; logged; least privilege” wording.

Olga/Daniel—can you share the latest approved phrasing from the claims matrix? If we can’t answer #4 as a hard “no,” let’s be clear on the default and the exception process.

Thanks,
Lukas

Lukas Meyer
Sales Engineering, EMEA | Redwood Inference

---

From: Marcus Lin <marcus.lin@redwoodinference.com>
To: Olga Petrov <olga.petrov@redwoodinference.com>, Daniel Carter <daniel.carter@redwoodinference.com>
Cc: Hannah Schmitt <hannah.schmitt@redwoodinference.com>, Lukas Meyer <lukas.meyer@redwoodinference.com>
Date: Wed, Feb 4, 2026 at 10:02 AM +0100
Subject: Re: Fwd: Nordbank – EU data residency questions (for questionnaire)

Adding a technical note to help with wording:

- For Hosted/Dedicated in an EU region: inference request handling + runtime execution occurs in-region.
- We do not enable cross-region replication of customer content by default.
- Service telemetry/logging: some metadata is collected for reliability/abuse prevention. The customer-facing phrasing should distinguish “customer content” vs “service metadata,” and note that retention is configurable by plan/deployment.
- Support access: can be necessary for incident response; access is gated, time-bound, and logged.

Olga/Daniel—happy to review the final block before it goes out.

Marcus

Marcus Lin
Security Engineering | Redwood Inference

---

From: Olga Petrov <olga.petrov@redwoodinference.com>
To: Hannah Schmitt <hannah.schmitt@redwoodinference.com>
Cc: Daniel Carter <daniel.carter@redwoodinference.com>, Lukas Meyer <lukas.meyer@redwoodinference.com>, Marcus Lin <marcus.lin@redwoodinference.com>
Date: Wed, Feb 4, 2026 at 12:11 PM +0100
Subject: Re: Fwd: Nordbank – EU data residency questions (for questionnaire)

Hannah—below is paste-ready, approved language for the questionnaire. Please use exactly as written (avoid strengthening “default” to “never”).

---
APPROVED WORDING (Hosted API – EU Region)

Q: Where is customer content processed?
A: When a customer selects an EU region for Redwood Hosted, customer content (e.g., prompts/inputs and model outputs) is processed within the selected EU region for the purpose of serving the request.

Q: Is customer content stored at rest by default? If yes, for how long and where?
A: By default, Redwood does not use customer prompts/inputs or model outputs to train foundation models. Storage and retention of customer content depend on the specific product configuration and customer agreement. Where storage is enabled for operational reasons (e.g., troubleshooting with customer approval), data is retained for a limited period and handled in accordance with Redwood’s data retention policy. Customers may request deletion as described in Redwood’s customer-facing retention/deletion overview.

Q: Where are logs stored?
A: Redwood generates service logs and audit/security-relevant logs to operate the service (e.g., authentication events, administrative actions, and system health telemetry). Log storage and retention depend on deployment mode and customer configuration. Redwood can support EU-region deployments where logs and related operational data are stored and processed in the selected EU region.

Q: Is any data replicated cross-region for DR/backups?
A: Redwood does not replicate customer content cross-region by default for EU-region deployments. Business continuity and disaster recovery controls are implemented at the service level; cross-region replication, if required, is handled only by explicit customer request/contracted configuration.

Q: Can support staff outside the EU access our data?
A: Redwood restricts internal access to customer data on a least-privilege basis. Where access is required for support or incident response, it is authorized, time-bound, and logged. Access may be performed by Redwood personnel located outside the EU, depending on the nature of the request and support coverage.

---
APPROVED WORDING (Dedicated – EU Region)

A: For Redwood Dedicated provisioned in an EU region, inference processing occurs within the selected EU region. Data handling, logging, and retention controls are configurable and aligned to the contracted deployment architecture. Redwood does not replicate customer content cross-region by default; any cross-region configuration would be an explicit, contracted exception.

---
APPROVED WORDING (Private – VPC / On‑Prem)

A: For Redwood Private, the customer controls where the system runs (e.g., customer VPC or on-prem). Customer content is processed within the customer-controlled environment. Operational telemetry and support access can be configured to meet customer requirements; any access is governed by customer-approved controls and auditing.

---
NOTES (do not include unless they ask):
- If they request the SOC 2 report, route via the evidence request playbook (NDA required).

Daniel—please confirm if you’d like me to add the one-pager draft as an attachment or keep responses “questionnaire-only.”

Olga

Olga Petrov
GRC Lead | Redwood Inference

---

From: Daniel Carter <daniel.carter@redwoodinference.com>
To: Hannah Schmitt <hannah.schmitt@redwoodinference.com>
Cc: Olga Petrov <olga.petrov@redwoodinference.com>, Lukas Meyer <lukas.meyer@redwoodinference.com>, Marcus Lin <marcus.lin@redwoodinference.com>
Date: Wed, Feb 4, 2026 at 12:28 PM +0100
Subject: Re: Fwd: Nordbank – EU data residency questions (for questionnaire)

Confirming Olga’s blocks are approved for external use.

On the attachment: you can include the data residency/retention one-pager as “informational,” but please label as “overview” and avoid implying it supersedes the MSA/DPA. If Nordbank needs a formal statement for their vendor file, keep it to the paste-ready answers.

If they press on “no non-EU access,” we should not commit; use the last paragraph in the support access answer.

Daniel

Daniel Carter
Compliance Evidence Reviewer | Redwood Inference

---

From: Hannah Schmitt <hannah.schmitt@redwoodinference.com>
To: Svenja Keller <svenja.keller@nordbank.example>
Cc: Tomasz Nowak <tomasz.nowak@nordbank.example>
Date: Wed, Feb 4, 2026 at 16:38 PM +0100
Subject: Re: Nordbank – EU data residency questions (for questionnaire)

Hi Svenja,

Thanks—below is paste-ready wording you can use in the questionnaire. This covers Hosted in an EU region, and also notes Dedicated and Private differences.

---
HOSTED API – EU REGION

1) Where are prompts/inputs and model outputs processed (EU vs US)?
When a customer selects an EU region for Redwood Hosted, customer content (e.g., prompts/inputs and model outputs) is processed within the selected EU region for the purpose of serving the request.

2) Are prompts/outputs stored at rest by default? If yes, for how long and where?
By default, Redwood does not use customer prompts/inputs or model outputs to train foundation models. Storage and retention of customer content depend on the specific product configuration and customer agreement. Where storage is enabled for operational reasons (e.g., troubleshooting with customer approval), data is retained for a limited period and handled in accordance with Redwood’s data retention policy. Customers may request deletion as described in Redwood’s customer-facing retention/deletion overview.

3) Where are logs stored (application logs, audit logs, access logs)?
Redwood generates service logs and audit/security-relevant logs to operate the service (e.g., authentication events, administrative actions, and system health telemetry). Log storage and retention depend on deployment mode and customer configuration. Redwood can support EU-region deployments where logs and related operational data are stored and processed in the selected EU region.

4) Is any data replicated cross-region for DR/backups?
Redwood does not replicate customer content cross-region by default for EU-region deployments. Business continuity and disaster recovery controls are implemented at the service level; cross-region replication, if required, is handled only by explicit customer request/contracted configuration.

5) Can support staff outside the EU access our data (even transiently), and under what controls?
Redwood restricts internal access to customer data on a least-privilege basis. Where access is required for support or incident response, it is authorized, time-bound, and logged. Access may be performed by Redwood personnel located outside the EU, depending on the nature of the request and support coverage.

6) Dedicated vs Private deployments
Dedicated (EU region): For Redwood Dedicated provisioned in an EU region, inference processing occurs within the selected EU region. Data handling, logging, and retention controls are configurable and aligned to the contracted deployment architecture. Redwood does not replicate customer content cross-region by default; any cross-region configuration would be an explicit, contracted exception.

Private (VPC/on‑prem): For Redwood Private, the customer controls where the system runs (e.g., customer VPC or on-prem). Customer content is processed within the customer-controlled environment. Operational telemetry and support access can be configured to meet customer requirements; any access is governed by customer-approved controls and auditing.
---

If helpful, I can also share a short overview one-pager on data residency/retention (informational) alongside the questionnaire responses.

Best,
Hannah

Hannah Schmitt
Account Executive, EMEA | Redwood Inference
hannah.schmitt@redwoodinference.com

Attachment: Redwood_Data-Residency-and-Retention_One-Pager_DRAFT_2026-02-03.pdf
