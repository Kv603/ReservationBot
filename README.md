# ReservationBot

ReservationBot is a multi-tenant Slack application for reserving shared resources that are owned by Slack channels. It is designed as a serverless AWS application with strict tenant isolation, centralized observability, and all infrastructure defined in AWS CDK.

## Architecture

- **Slack ingress:** API Gateway HTTP API receives Slack slash commands, events, and interactive payloads.
- **Application runtime:** AWS Lambda runs the Slack adapter and reservation domain services.
- **Data store:** DynamoDB single-table design with tenant-scoped partition keys. Every Slack workspace gets isolated JSON document collections under `TENANT#{workspace_id}`.
- **Async work:** EventBridge Scheduler and SQS handle reminders, retries, and delayed Slack messages.
- **Secrets:** AWS Secrets Manager stores Slack app credentials.
- **Observability:** CloudWatch structured logs, X-Ray tracing, alarms, and Lambda Powertools-ready log fields.

## Core Capabilities

- Full workspace isolation by Slack team or enterprise workspace ID.
- Tenant-customizable bot profile, commands, message templates, policies, reminders, and role grants.
- Workspace-admin controlled global settings for bot name/avatar, alias slash commands, duration limits, private reminders, and audit destination.
- Slack `<!date>` message formatting with workspace-timezone plaintext fallbacks.
- User timezone preference for direct reminder text when available, falling back to the Slack workspace timezone.
- Audit notifications for resource, reminder, settings, and reservation changes.
- Channel-owned resources with default channel manager administration.
- Role grants to Slack users and Slack user groups.
- Resource types:
  - **specific:** one named item, such as `3d-printer`.
  - **fungible:** a pool with capacity, such as `parking-space` with 20 equivalent slots.
  - **container:** a parent resource that blocks reservations for child resources during the same time.
- Conflict-safe reservation creation using transactional DynamoDB writes and domain-level overlap checks.
- Per-tenant, per-channel, and per-resource access policies.
- Optional reservation reason text, preserving Slack markdown and emojis.
- Default reservation duration policy: minimum 15 minutes, maximum 12 hours. Channels and resources can override it.
- Default private reservation reminders are off; admins can set a lead time from 15 minutes to 24 hours.
- Public scheduled reminders for daily or weekly resource reservation posts, with Slack `ts` tracking for future `chat.update` refreshes.
- Dialog-based reservation flows from `/reserve` and tenant-configured slash aliases.

## Repository Layout

```text
infra/                 AWS CDK app and stacks
api/                   Vercel ASGI app, Lambda handler, and domain services
tests/                 Unit tests for reservation logic and auth rules
docs/                  Architecture and operational notes
```

## Slack App Manifest

The Slack Marketplace and Enterprise Grid-ready manifest lives at [docs/slack-app-manifest.json](C:/Users/passp/Documents/Codex/resbot/ReservationBot/docs/slack-app-manifest.json). Replace the placeholder `https://reservationbot.example.com` URLs with the deployed API Gateway/custom-domain URLs before importing it into Slack.

Scope rationale and Marketplace notes are documented in [docs/SLACK_MARKETPLACE.md](C:/Users/passp/Documents/Codex/resbot/ReservationBot/docs/SLACK_MARKETPLACE.md).

## Slack Messaging

ReservationBot embeds dates and times with Slack's `<!date>` command so each viewer sees localized time in Slack. The plaintext fallback is always rendered in the workspace timezone from Slack `team.info`.

Private reservation reminders are sent as DMs when enabled and include reservation details plus Confirm, Edit, and Cancel buttons.

Public scheduled reminders can post today's, tomorrow's, or the upcoming week's reservations. Each schedule can target the owning resource channel by default, another Slack channel, or a Slack user. Schedules can cover all resources in one channel, a specific resource list, or admin-only `EVERYTHING` across the tenant.

Typing `/reserve` with no arguments opens a Slack modal for channels with reservable resources. Users can choose a resource, date, start time, duration, and optional Reason. Typing `/reserve cancel` or `/reserve my reservations` opens a modal listing the user's own future reservations so they can cancel one. Typing `/reserve edit <reservation-id>` opens the reservation modal prefilled for that reservation.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m unittest discover -s tests
cd infra
cdk synth
```

For Vercel, the backend entrypoint is [api/index.py](C:/Users/passp/Documents/Codex/resbot/ReservationBot/api/index.py), which exports the top-level ASGI `app` variable Vercel expects. The deployment configuration is [vercel.json](C:/Users/passp/Documents/Codex/resbot/ReservationBot/vercel.json).

Set these context values or environment variables before deploying:

- `slackSigningSecretArn`
- `slackBotTokenSecretArn`
- `stageName` defaults to `dev`

```powershell
cd infra
cdk deploy -c slackSigningSecretArn=arn:aws:secretsmanager:... -c slackBotTokenSecretArn=arn:aws:secretsmanager:...
```
