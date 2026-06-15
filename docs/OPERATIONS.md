# Operations

## Deployment

All infrastructure is deployed with AWS CDK. Do not create production resources manually in the AWS console.

Required secrets:

- Slack signing secret in AWS Secrets Manager.
- Slack bot token in AWS Secrets Manager.

## Runbooks

### Slack command latency alarm

1. Check API Gateway `5XX` and Lambda `Errors`.
2. Check Lambda duration p95 and throttles.
3. If Slack retries are elevated but Lambda is healthy, inspect outbound Slack API rate-limit logs.
4. Replay failed async work from the DLQ after resolving the root cause.

### Reservation conflicts reported incorrectly

1. Query the tenant/resource GSI for the affected time window.
2. Inspect the resource hierarchy document.
3. Run the overlap case through `tests/test_reservations.py`.
4. If a race is suspected, inspect DynamoDB transaction cancellation reasons.

### Tenant isolation audit

1. Verify each record `PK` begins with `TENANT#{tenant_id}`.
2. Confirm no handler accepts tenant IDs from unverified request bodies.
3. Review CloudWatch logs for cross-tenant access warnings.

### Audit destination changed unexpectedly

1. Query `AUDIT#` records for the tenant and look for `audit_destination_changed` or `audit_destination_removed`.
2. Confirm the actor was a Slack admin or workspace owner at the time of the request.
3. Check the old audit destination for the final change notification.
4. If outbound posting failed, replay from the durable audit event.
