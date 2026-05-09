# Integration Support Notes

Webhook delivery retries use exponential backoff for 24 hours. If all webhook
delivery retries fail, the integration event is parked in the failed-webhook
queue for manual replay.

Slack ticket sync uses the shared Slack ticket channel for team plans and the
named incident channel for enterprise Severity 1 incidents. Starter plans do not
include Slack ticket sync.

The CRM integration writes customer identifiers, plan tier, and renewal date.
It does not write API tokens or support ticket message bodies to the CRM.

Idempotency keys are required for ticket-import webhooks. If duplicate import
events are detected, support operations reviews the import manifest before a
manual replay is approved.
