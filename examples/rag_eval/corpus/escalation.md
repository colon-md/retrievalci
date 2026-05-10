# Escalation Runbook

Severity 1 incidents page the on-call engineer immediately. The engineer opens
the Retrieval Health dashboard first, then checks the queue-depth chart and the
latest deploy marker.

If Recall@5 drops below 0.90 for the support retriever, the support search team
is paged. The incident owner rolls back the retriever prompt bundle before
changing the embedding model.

Severity 2 incidents create a ticket for the next business day unless a customer
contract says otherwise.

The incident commander posts customer-facing updates every 30 minutes during a
Severity 1 incident. When a named incident channel exists, the same update is
mirrored there after the public status page is updated.

Post-incident reviews are due within three business days. The review must link
the RetrievalCI run artifact that detected or ruled out a retrieval regression.
