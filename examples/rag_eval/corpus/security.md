# Security Operations Notes

API tokens must be stored in the customer's secret manager and rotated every 90
days. Tokens must not appear in RetrievalCI trace artifacts, support tickets, or
RAG evaluation reports.

Enterprise workspaces can require SAML SSO. When SAML SSO is required, local
password login is disabled for managed users after the identity provider
metadata is verified.

The standard data residency region is us-east-1. Enterprise contracts may name
eu-central-1 as the residency region, but support engineers must confirm the
contract flag before promising the region to a customer.

Security review requests are routed to the trust queue. The trust queue responds
within two business days for enterprise accounts and within five business days
for starter and team accounts.
