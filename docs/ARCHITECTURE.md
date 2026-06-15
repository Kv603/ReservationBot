# ReservationBot Architecture

## Tenancy Model

ReservationBot uses Slack `team_id` as the primary tenant identifier. For Enterprise Grid installations, the tenant key can be configured to use `enterprise_id:team_id` when workspace-level isolation is required, or `enterprise_id` when the customer explicitly wants enterprise-wide policy sharing.

All persisted records are JSON documents in DynamoDB with a tenant-prefixed partition key:

```text
PK = TENANT#{tenant_id}
SK = {COLLECTION}#{entity_id}
```

Reservation lookup uses a resource/time index:

```text
GSI1PK = TENANT#{tenant_id}#RESOURCE#{resource_id}
GSI1SK = START#{start_epoch}#RESERVATION#{reservation_id}
```

This gives every tenant separate logical collections while still using a managed serverless NoSQL backend. Tenant IDs are never accepted from user input alone; they are derived from verified Slack payloads.

Tenant global settings are stored under `SETTINGS#GLOBAL`. Per-channel settings are stored under `CHANNEL#{channel_id}#SETTINGS`, and resource-specific overrides live inside the resource JSON document.

## Service Boundaries

- **Slack adapter:** verifies signatures, normalizes Slack commands and interactions, and emits application commands.
- **Tenant service:** loads tenant configuration, role grants, policy inheritance, and Slack customization.
- **Resource service:** owns channel resource definitions and hierarchy validation.
- **Reservation service:** owns availability checks, conflict detection, and transactional reservation writes.
- **Settings service:** owns workspace-admin changes for bot profile, slash command aliases, audit destination, duration limits, timezone, and reminder defaults.
- **Audit service:** persists audit events and posts them to the configured Slack channel or user.
- **Reminder service:** owns public schedules and direct-message reminders via EventBridge Scheduler and SQS.
- **Modal service:** composes Slack modal views for reservation creation/editing and user-owned reservation cancellation, then parses `view_submission` payloads back into domain commands.

The first implementation is a modular monolith deployed as one Lambda or one Vercel Python function. Backend source lives under `api/` so Vercel can discover the ASGI app at `api/index.py`, while AWS CDK packages the same directory as the Lambda asset. The boundaries are explicit Python modules so high-volume tenants can later be split into independent functions without changing the public API.

## Slack App Distribution

The production Slack manifest is stored at `docs/slack-app-manifest.json`. It is structured for Slack Marketplace review and Enterprise Grid deployment:

- Organization deployment is enabled.
- Socket Mode is disabled in favor of HTTPS request URLs backed by API Gateway.
- Token rotation is enabled.
- OAuth scopes are limited to command handling, message posting/updating, channel/user/user-group metadata, direct-message opening, and workspace metadata.
- No history, file, admin, SCIM, or Audit Logs API scopes are requested.

Before Marketplace submission, replace placeholder request and redirect URLs with the production custom domain and configure Slack distribution settings for support, privacy, terms, and security contact information.

## Authorization Defaults

- Slack workspace admins and owners have full tenant access.
- Slack workspace admins and owners can change workspace global settings.
- Slack channel managers have full control over resources assigned to that channel.
- Channel members can list resources and manage their own reservations by default.
- Tenant, channel, and resource policies can restrict creators/managers to specific Slack users or Slack user groups.
- Deny rules take precedence over allow rules.

## Reservation Conflicts

ReservationBot treats resource conflicts as interval overlap checks within the same tenant:

- Specific resources allow one overlapping reservation.
- Fungible resources allow overlapping reservations up to `capacity`.
- Container resources block all descendants.
- Descendant reservations block parent container reservations.
- Sibling child resources do not conflict unless their parent is reserved.

Conflict decisions are made in the domain layer and persisted with DynamoDB transactions. The production write path is expected to add a short lock item per resource/time bucket for high-contention resources.

Reservations may include an optional freeform `reason`. The value is treated as Slack mrkdwn-compatible text and may contain emojis and markdown.

## Slack Dialogs

ReservationBot uses Slack modals, the current replacement for legacy Slack dialogs. A slash command invocation provides the `trigger_id` required by `views.open`.

Dialog entry points:

- `/reserve` opens a create-reservation modal when the channel has reservable resources.
- Any tenant-configured reservation slash-command alias opens the same flow.
- `/reserve edit <reservation-id>` opens the same modal prefilled for a user's own reservation.
- `/reserve cancel`, `/reserve list`, or `/reserve my reservations` opens a modal listing the user's active future reservations and lets them cancel one.

The modal collects resource, date, start time, duration, and optional Reason. Availability conflicts are validated on submit by the reservation service before creating or editing the reservation.

## Settings

Workspace global settings include:

- Bot display name and avatar URL.
- Additional slash command aliases.
- Workspace timezone from Slack `team.info`.
- Audit destination, which can be a Slack channel or user ID.
- Global reservation duration policy. Defaults are 15 minutes minimum and 12 hours maximum.
- Private reservation reminder default. The value is either off or a lead time from 15 minutes to 24 hours.

Channel and resource duration policies override the global policy. Resource policy has the highest precedence, then channel policy, then workspace policy.

## Timezones And Slack Dates

Python logging uses the execution environment timezone by configuring standard log formatters with `time.localtime`.

Slack messages embed dates and times using `<!date>` tokens. Slack renders these in each viewer's timezone. The fallback text is always formatted in the workspace timezone from Slack `team.info`. Direct reminders may include user-local plaintext context when a user timezone is available; otherwise they fall back to the workspace timezone.

## Audit

Workspace owners and admins can set, change, or remove the audit destination. All resource, reminder, settings, and reservation changes are persisted as audit events and posted to the current audit destination when configured. Audit messages mention the acting Slack user with `<@user_id>` and may include the Slack handle when available.

When the audit destination is changed or removed, ReservationBot sends one final audit message to the old destination before using the new destination or disabling outbound audit posts.

## Scheduled Public Reminders

Scheduled reminders are tenant JSON documents under `REMINDER#{reminder_id}`. A reminder includes:

- Title included in each post.
- Time of day in minutes after midnight, validated to 15-minute increments.
- Posting schedule: daily or weekly.
- Timeframe to display: today, tomorrow, or the upcoming week.
- Optional destination channel or user. When omitted, the resource-owning source channel is used.
- Optional suppression of empty posts.
- Resource scope: channel default, specific resource IDs, or admin-only `EVERYTHING`.
- Last Slack post `ts`.

When a schedule posts successfully, the returned Slack `ts` is stored on the reminder. If a scheduled run is skipped because there are no matching reservations and suppression is enabled, `last_post_ts` is cleared.

When a reservation changes inside a schedule's current display timeframe after the schedule has posted, the reminder service renders the body again. If `last_post_ts` exists, it updates the existing Slack post via `chat.update`; otherwise it posts a fresh message and saves the returned `ts`.

Workspace admins and owners can manage all scheduled reminders. Channel managers can view, add, edit, pause, or delete reminders that post to Slack channels they manage.

## Resiliency

- Slack endpoints return quickly and push long-running work to SQS.
- Outbound Slack API calls are retried with jitter and respect rate-limit responses.
- Failed async jobs land in dead-letter queues.
- DynamoDB uses point-in-time recovery and AWS-managed encryption.
- Lambda has bounded reserved concurrency to protect downstream Slack APIs.

## Observability

Every log line includes:

- `tenant_id`
- `slack_team_id`
- `slack_channel_id`
- `correlation_id`
- `command_type`

CDK enables Lambda tracing, API access logs, DynamoDB alarms, and DLQ alarms.
