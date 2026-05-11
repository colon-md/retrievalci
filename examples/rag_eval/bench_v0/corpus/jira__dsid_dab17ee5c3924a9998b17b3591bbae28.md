# Sup 18477 Restore Fails Onprem Missing Crds

Source type: jira
Document ID: dsid_dab17ee5c3924a9998b17b3591bbae28
Release: v1.0.0
Benchmark: EnterpriseRAG-Bench synthetic upstream data
On-prem restore fails during manifest apply: missing CRDs / resource ordering causes "no matches for kind" errors

Issue summary
- Customer attempted a control-plane restore for Redwood Private in an air-gapped on-prem environment.
- `restore apply` fails when applying Kubernetes resources from the backup artifact.
- Errors indicate missing CRDs and/or incorrect ordering (CustomResources applied before CRDs are installed).

Impact
- Customer blocked from completing restore validation for internal DR readiness review.
- No production outage (this was a planned restore drill into a new cluster), but DR timeline at risk.

Customer context
- Customer: MedData Systems
- Deployment: Redwood Private (on-prem, air-gapped)
- Restore target: new Kubernetes cluster (fresh install)
- Backup artifact source: previous cluster (same environment) created via installer `backup create`.

What the customer expected
- Restore should bootstrap required components in a deterministic order and fail fast with clear remediation steps if prerequisites are missing.

What happened
- Restore job attempts to apply Kubernetes manifests (including CRs for cert-manager / external-secrets / redwood control-plane CRs) before the corresponding CRDs exist on the target cluster.

Steps to reproduce (as reported)
1) Stand up a fresh Kubernetes cluster (air-gapped, no external repos).
2) Install Redwood Private base prerequisites (customer believed CRDs would be included in restore bundle).
3) Run: `redwood-private restore apply --from /mnt/backup/backup_2025-02-17T0312Z`.
4) Restore fails during "Apply Kubernetes objects" stage.

Observed errors (snippets)
- kubectl apply output from restore logs:
  - "error: resource mapping not found for name: \"redwood-control-plane\" namespace: \"redwood-system\" from \"manifest.yaml\": no matches for kind \"RedwoodTenant\" in version \"platform.redwood.ai/v1\" ensure CRDs are installed first"
  - "error: unable to recognize \"manifest.yaml\": no matches for kind \"ExternalSecret\" in version \"external-secrets.io/v1beta1\""
  - "error: unable to recognize \"manifest.yaml\": no matches for kind \"Certificate\" in version \"cert-manager.io/v1\""
- Restore summary:
  - "stage=apply_k8s_objects status=failed applied=214 failed=17 skipped=0"
  - "hint: install required CRDs and re-run restore"

Notes
- The customer’s baseline cluster did not have cert-manager and external-secrets CRDs preinstalled in the restore target.
- In air-gapped mode, the customer is using the offline bundle and does not have access to pull charts/images during restore.

Request
- Provide a clear supported workaround for installing required CRDs (and in what order) prior to re-running restore.
- Confirm whether Redwood’s restore workflow should install CRDs automatically (from bundle) or explicitly require them as prerequisites.
- Update customer-facing runbook to prevent recurrence.
2025-02-18 09:14 PT — Fatima Noor (Reporter): Created from customer escalation in #support. Customer blocked on restore drill; attaching initial error snippets and requesting logs + exact installer version.
2025-02-18 10:02 PT — Support (Fatima Noor): Requested: (1) installer version (`redwood-private version`), (2) backup manifest header (format/version), (3) full restore log, (4) list of preinstalled CRDs on target cluster (`kubectl get crd | wc -l` plus grep for cert-manager/external-secrets/platform.redwood.ai).
2025-02-18 13:37 PT — Customer (MedData Ops): Provided logs. Installer: 1.9.3-private (offline bundle). Backup created 2025-02-17. Target cluster has no cert-manager CRDs. Error repeats for ExternalSecret and RedwoodTenant kinds. They assumed restore would include everything required.
2025-02-18 14:10 PT — Sean Gallagher (Assignee, SRE): Confirmed symptom aligns with ordering: restore applying CRs before CRDs are present. In air-gapped mode, CRDs must come from the offline bundle (not fetched). Looping in Ethan Park for control-plane restore sequencing expectations.
2025-02-18 15:03 PT — Ethan Park (Control plane eng lead): Restore workflow currently assumes prerequisite operators/CRDs exist (cert-manager, external-secrets, and Redwood platform CRDs via helm install of control-plane chart). If customer ran restore on a completely fresh cluster without running the "bootstrap" step, CRDs will be missing. We should improve preflight to detect and provide a deterministic checklist, and optionally extract CRDs from bundle for install-first.
2025-02-18 16:22 PT — Fatima Noor (Support): Sent interim workaround to customer: install prerequisite CRDs first (from offline bundle) then re-run restore. Asked customer to confirm whether they can run helm from bundle and whether images are already loaded into their registry.
2025-02-19 09:05 PT — Customer (MedData Ops): They can run helm from the bundle; images are preloaded. They need exact commands and ordering. Also asked whether Redwood secrets are restored or only references.
2025-02-19 10:18 PT — Sean Gallagher (SRE): Provided ordered workaround (below). Also clarified secrets handling: restore rehydrates secret references/config; secret material depends on customer’s secret manager and must be available post-restore (no raw secret export).
2025-02-19 10:20 PT — Sean Gallagher (SRE) — Workaround details shared:
1) Verify CRDs missing:
   - `kubectl get crd | egrep '(cert-manager|external-secrets|platform.redwood.ai)' || true`
2) From the offline bundle, install prereq charts WITH CRDs enabled (do not use `--skip-crds`):
   - cert-manager: `helm upgrade --install cert-manager ./charts/cert-manager -n cert-manager --create-namespace --set installCRDs=true`
   - external-secrets: `helm upgrade --install external-secrets ./charts/external-secrets -n external-secrets --create-namespace`
3) Install Redwood control-plane chart to lay down Redwood CRDs before applying CR instances:
   - `helm upgrade --install redwood-control-plane ./charts/redwood-control-plane -n redwood-system --create-namespace`
4) Confirm CRDs exist:
   - `kubectl get crd | egrep '(cert-manager.io|external-secrets.io|platform.redwood.ai)'`
5) Re-run restore:
   - `redwood-private restore apply --from /mnt/backup/backup_2025-02-17T0312Z --continue-on-preflight=false`
6) Validation:
   - `kubectl -n redwood-system get pods`
   - run the post-restore health check in the runbook (control-plane API ready, tenant config present, routing policy objects present).

Notes: If the cluster has partially-applied CRs, delete the failed CRs before re-run to avoid immutable field conflicts.
2025-02-19 13:44 PT — Customer (MedData Ops): Workaround succeeded after installing CRDs/operators first. Restore completed, but they noted the installer error message was generic and the runbook didn’t emphasize CRD prerequisites for a brand new cluster.
2025-02-20 08:55 PT — Fatima Noor (Support): Requested customer confirm final validation checklist results (tenant config restored, routing policies present, control-plane endpoints healthy).
2025-02-20 12:11 PT — Customer (MedData Ops): Validation looks good. They asked Redwood to document "fresh cluster" prerequisites and add a preflight check that fails with specific missing CRDs listed.
2025-02-21 09:32 PT — Sean Gallagher (SRE): Logged follow-up requests for Eng: add explicit preflight CRD checks + improve ordering (CRDs before CRs). Referencing ongoing restore workflow improvements and helm hook work. Linking internal work to ENG-9822/ENG-9830/ENG-9828.
2025-02-22 10:05 PT — Fatima Noor (Support): Marking Resolved. Customer unblocked with documented workaround; awaiting doc patch in next runbook refresh.
Restore failure was due to missing prerequisite CRDs (cert-manager, external-secrets, Redwood platform CRDs) on a fresh on-prem cluster, causing "no matches for kind" during manifest apply. Workaround: install prerequisite charts/CRDs from the offline bundle first, then re-run `restore apply`. Follow-ups filed to improve restore preflight and documentation to explicitly cover fresh-cluster ordering.
Install prerequisite CRDs/operators from the offline bundle (cert-manager with installCRDs=true, external-secrets, then Redwood control-plane chart to install Redwood CRDs) before running `redwood-private restore apply`. If partial CRs were applied, delete failed CR objects prior to re-run to avoid conflicts.
Redwood will update the customer-facing restore runbook to add a "fresh cluster prerequisites" section and enhance installer preflight to list missing CRDs explicitly (targeting the next Private installer + docs release).
This ticket is consistent with recent restore drill feedback: restore should (a) detect missing CRDs and stop early with actionable output, and (b) optionally offer a "bootstrap prerequisites" step in air-gapped mode using bundle-contained CRD manifests. Coordinate with docs owner (Elliot Price / Kira Thompson) and control-plane eng (Ethan Park).
Slack: #support escalation (see internal thread linked from SUP-18421)
Confluence: runbook-control-plane-restore (add prereq CRD section; emphasize ordering for fresh clusters)
GitHub: pr-489 (restore workflow + validation), pr-138 (restore job template/hooks), pr-512 (compatibility/versioning messaging)
