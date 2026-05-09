# Billing Support Notes

The Atlas Helpdesk starter plan keeps audit log entries for 30 days. The team
plan keeps audit log entries for 180 days and exports them each night to the
customer's storage bucket.

Invoice retries run three times over seven days. If all retries fail, the
account moves to read-only mode, but existing support tickets remain visible to
the customer.

Refund requests are handled by the billing queue. The refund assistant may draft
the response, but a human billing reviewer must approve refunds above 100 USD.

Annual contracts receive a usage true-up on the first business day of each
month. The billing operations queue must send the true-up summary before the
customer success manager schedules a renewal call.

Plan downgrades are queued for the next billing cycle. A downgrade never deletes
historical tickets, audit exports, or incident review records.
