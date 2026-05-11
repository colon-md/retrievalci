# 20250520 Trident Financial Dpa And Sla Redlines

Source type: gmail
Document ID: dsid_98d7acbda29a4260a413c8d6da7207a3
Release: v1.0.0
Benchmark: EnterpriseRAG-Bench synthetic upstream data
Re: Trident Financial Services — DPA + SLA redlines (Private/VPC)

From: Elaine Porter <elaine.porter@tridentfs.com>
To: Michael Grant <michael.grant@redwoodinference.com>
Cc: Raj Mehra <raj.mehra@tridentfs.com>, Maya Chen <maya.chen@tridentfs.com>, Avery Johnson <avery.johnson@redwoodinference.com>
Date: Tue, May 20, 2025 at 9:12 AM
Subject: Trident Financial Services — DPA + SLA redlines (Private/VPC)

Michael,

Attached are Trident’s redlines to (i) the DPA and (ii) the SLA. Key items called out in comments in the docs:

1) Data residency: require “no processing outside the United States” and restrictions on subcontractors.
2) Retention: require 0-day retention by default; deletion within 24 hours of request.
3) Audit logs: require export to Trident-controlled SIEM; log retention 7 years.
4) Security incident notice: 24-hour notification.
5) Liability: increase caps + remove carve-outs.
6) SLA: increase service credits; add RTO/RPO language.

Please confirm whether Redwood can agree to these as written, and if not, provide counter language.

Regards,
Elaine Porter
Senior Counsel, Technology
Trident Financial Services

Attachments:
- Trident_Redwood_DPA_Redlines_2025-05-19.docx
- Trident_Redwood_SLA_Redlines_2025-05-19.docx


---

From: Avery Johnson <avery.johnson@redwoodinference.com>
To: Michael Grant <michael.grant@redwoodinference.com>
Cc: Stephanie Nguyen <stephanie.nguyen@redwoodinference.com>
Date: Tue, May 20, 2025 at 9:24 AM
Subject: Fwd: Trident Financial Services — DPA + SLA redlines (Private/VPC)

Fwd’ing Trident redlines. They’re pushing hard on “US-only processing” + long audit log retention. This is for Private (VPC) deployment.

We need a quick turnaround — procurement wants a same-week response.

Avery


---

From: Michael Grant <michael.grant@redwoodinference.com>
To: Sofia Mendes <sofia.mendes@redwoodinference.com>, Naomi Feldman <naomi.feldman@redwoodinference.com>
Cc: Dr. Aisha Rahman <aisha.rahman@redwoodinference.com>, Vivek Kulkarni <vivek.kulkarni@redwoodinference.com>, Stephanie Nguyen <stephanie.nguyen@redwoodinference.com>, Avery Johnson <avery.johnson@redwoodinference.com>
Date: Tue, May 20, 2025 at 10:03 AM
Subject: Trident — DPA/SLA redlines (need positions today)

Team — pls review Trident redlines (attached). Need positions + proposed counter language today.

Top risk flags / where we likely need to counter:

A) Data residency / “no processing outside US”
- We can generally commit to processing in the customer-selected region for Private/VPC, but need to be careful about support access, telemetry, and backups. We should avoid an absolute “no bytes ever leave US” statement.
- Ask: Naomi/Aisha — what can we safely say re: support systems and subprocessors? Any standard language for “support may access from outside but data remains in-region” vs “support access limited to US persons” etc.

B) Retention / deletion
- They want 0-day retention by default + deletion within 24 hours.
- Need to align with product realities: request logs / audit logs retention is configurable; but 0-day might break ops/troubleshooting.
- We can offer tight retention for request payloads and define audit logs separately (audit logs are not the same as prompts/responses).

C) Audit logs / 7-year retention + SIEM export requirement
- We can offer export hooks (customer-controlled destination) + sample schema, but 7-year retention in our systems is a commercial/compliance add-on question and probably “customer retains in their SIEM” rather than us.

D) Incident notice: 24 hours
- Our default is typically “without undue delay” / 72 hours for certain events; 24 hours is aggressive. We may be able to do 24 hours for confirmed material incidents impacting their data, but need to define “security incident” and “confirmed” / “material.”

E) Liability / cap changes
- Likely business approval required. Sofia — pls advise if we should hold the line on cap and carve-outs.

F) SLA credits + RTO/RPO
- We can discuss service credits; RTO/RPO may be for enterprise DR options only, not base.

Goal: send Elaine/Raj a consolidated response today with a redline package.

Michael

Attachments:
- Trident_Redwood_DPA_Redlines_2025-05-19.docx
- Trident_Redwood_SLA_Redlines_2025-05-19.docx


---

From: Naomi Feldman <naomi.feldman@redwoodinference.com>
To: Michael Grant <michael.grant@redwoodinference.com>, Sofia Mendes <sofia.mendes@redwoodinference.com>
Cc: Dr. Aisha Rahman <aisha.rahman@redwoodinference.com>, Vivek Kulkarni <vivek.kulkarni@redwoodinference.com>, Stephanie Nguyen <stephanie.nguyen@redwoodinference.com>, Avery Johnson <avery.johnson@redwoodinference.com>
Date: Tue, May 20, 2025 at 11:06 AM
Subject: Re: Trident — DPA/SLA redlines (need positions today)

Quick take (security/compliance):

A) Residency
- For Private (VPC), we can commit that customer content (prompts/outputs) is processed and stored within the deployed region/VPC per the architecture. We should NOT commit that “no Redwood personnel outside US can ever access.” Better: access is controlled, logged, and limited to authorized personnel on a need-to-know basis; remote access can be restricted by customer policy in the Private deployment.
- Subprocessors: in Private/VPC, this is largely their cloud provider + any Redwood support tooling (if enabled). We should require a defined list / allow updates with notice.

B) Retention
- Separate: (1) customer content retention vs (2) operational/audit logs.
- We can offer “no training on customer data” language if not already in the DPA; and for retention, propose “customer-configurable retention settings; default per product; can be set to short windows; deletion upon request subject to legal/operational requirements.”
- 24 hours hard SLA on deletion is tough; propose “within 30 days” standard, with best efforts for faster.

C) Audit logs / SIEM export
- We can support audit logging and export. We should counter that 7-year retention is on customer side (their SIEM). We can commit we provide log export and the customer can retain as long as they need.

D) Incident notice
- Recommend counter: notify without undue delay and in any event within 72 hours of confirmation of a Security Incident affecting Customer Data.
- If business wants to concede: 48 hours post-confirmation for “confirmed material incidents.”

I can help wordsmith but need Sofia/legal to decide positions.

Naomi


---

From: Dr. Aisha Rahman <aisha.rahman@redwoodinference.com>
To: Michael Grant <michael.grant@redwoodinference.com>, Naomi Feldman <naomi.feldman@redwoodinference.com>, Sofia Mendes <sofia.mendes@redwoodinference.com>
Cc: Vivek Kulkarni <vivek.kulkarni@redwoodinference.com>, Stephanie Nguyen <stephanie.nguyen@redwoodinference.com>, Avery Johnson <avery.johnson@redwoodinference.com>
Date: Tue, May 20, 2025 at 11:22 AM
Subject: Re: Trident — DPA/SLA redlines (need positions today)

+1 to Naomi’s notes.

Strongly recommend we avoid absolute commitments that could be interpreted as “no cross-border access ever.” We can commit to:

- in-region processing/storage for Customer Content for Private/VPC deployments;
- access controls (RBAC), MFA/SSO where applicable, and auditability of admin actions;
- audit log export capability so Trident can satisfy long-term retention in their own systems.

For incident notification: 24h is not something I’d sign as a default. If we make it “within 24 hours of confirmation of a material Security Incident,” that’s safer but still requires on-call process maturity. Prefer 72h.

Aisha


---

From: Sofia Mendes <sofia.mendes@redwoodinference.com>
To: Michael Grant <michael.grant@redwoodinference.com>
Cc: Naomi Feldman <naomi.feldman@redwoodinference.com>, Dr. Aisha Rahman <aisha.rahman@redwoodinference.com>, Stephanie Nguyen <stephanie.nguyen@redwoodinference.com>, Avery Johnson <avery.johnson@redwoodinference.com>
Date: Tue, May 20, 2025 at 1:04 PM
Subject: Re: Trident — DPA/SLA redlines (need positions today)

Commercial/legal positions:

Liability:
- Hold line on our standard cap + standard carve-outs. If Trident insists on higher cap, that’s business approval (Stephanie) + likely pricing adjustment.
- No removal of exclusion of consequential damages.

SLA:
- We can offer incremental service credits within a defined monthly cap. Avoid open-ended credits.
- RTO/RPO only if we explicitly sell DR/secondary region; otherwise “commercially reasonable efforts” + incident comms.

DPA:
- Residency: okay to commit to in-region processing/storage for Private/VPC; reject absolute “no processing outside US” if it constrains support/metadata.
- Subprocessors: standard list + notice/update mechanism.
- Retention: counter 0-day default + 24h deletion. Suggest “configurable retention” and a reasonable deletion timeframe.

If you want, I can consolidate into a short redline memo + draft email to Elaine.

Sofia


---

From: Michael Grant <michael.grant@redwoodinference.com>
To: Elaine Porter <elaine.porter@tridentfs.com>
Cc: Raj Mehra <raj.mehra@tridentfs.com>, Maya Chen <maya.chen@tridentfs.com>, Avery Johnson <avery.johnson@redwoodinference.com>
Date: Tue, May 20, 2025 at 4:37 PM
Subject: Re: Trident Financial Services — DPA + SLA redlines (Private/VPC)

Elaine — thank you. We reviewed and prepared a set of counters that we believe addresses Trident’s core requirements for a Private (VPC) deployment while staying aligned with our operational and security commitments.

Attached: Redwood_Proposed_DPA_SLA_Counters_Trident_2025-05-20.docx

High-level summary of key positions:

1) Data residency / processing location
- For a Private (VPC) deployment, we can commit that Customer Content (e.g., prompts and outputs) will be processed and stored within the agreed deployment region/VPC.
- We proposed edits to avoid an absolute prohibition that could unintentionally restrict necessary support and security operations. We included language that any administrative access is controlled, limited, and auditable.

2) Retention / deletion
- We proposed separating (a) retention of Customer Content from (b) retention of operational/audit logs.
- We can support configurable retention settings consistent with the Private deployment architecture. We countered the “0-day retention by default” and “24-hour deletion” requirements with a more operationally feasible deletion standard while still meeting the underlying objective (minimizing retention of customer content).

3) Audit logs / SIEM export / 7-year retention
- We can support audit logging and export so Trident can ingest logs into its SIEM and retain them for 7 years (or longer) under Trident’s control.
- We proposed edits so long-term retention is satisfied via export/retention in Trident systems, rather than requiring Redwood to retain logs for 7 years.

4) Security incident notice
- We proposed notification “without undue delay” and within a defined window following confirmation of a Security Incident affecting Customer Data.

5) Liability and SLA credits
- We proposed standard positions on liability and a bounded service credit structure.

If helpful, I can jump on a 30-minute working session tomorrow with you + Raj/Maya to walk through the edits line-by-line and align on any remaining gaps.

Best,
Michael Grant
Contracting Lead (Legal)
Redwood Inference

Attachment:
- Redwood_Proposed_DPA_SLA_Counters_Trident_2025-05-20.docx


---

From: Raj Mehra <raj.mehra@tridentfs.com>
To: Michael Grant <michael.grant@redwoodinference.com>
Cc: Elaine Porter <elaine.porter@tridentfs.com>, Maya Chen <maya.chen@tridentfs.com>, Avery Johnson <avery.johnson@redwoodinference.com>
Date: Tue, May 20, 2025 at 5:48 PM
Subject: Re: Trident Financial Services — DPA + SLA redlines (Private/VPC)

Michael,

Got it — thank you for the quick turn. We’ll review the counter doc tonight.

Can you hold time tomorrow 11:00–11:30am PT for a working session? Security will want to focus on (i) the “in-region” language and (ii) audit log retention/export responsibilities.

Raj Mehra
Vendor Management
Trident Financial Services
