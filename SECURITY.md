# Security

This experimental integration is not a security-reviewed product. Keep the relay
bound to loopback and use authenticated HTTPS for tablet ingress. Store device
and relay credentials separately in private files with restrictive permissions.
Never include credentials, real captures, notebook identifiers or private logs in
an issue. Do not expose tablet SSH through the message relay.

For a suspected vulnerability, use GitHub's private vulnerability reporting if
it is enabled for this repository. Otherwise open a minimal issue asking for a
private contact route without disclosing exploit details or sensitive data.
Maintainers should rotate affected credentials and preserve durable receipts for
reconciliation after an incident; do not blindly replay uncertain deliveries.
