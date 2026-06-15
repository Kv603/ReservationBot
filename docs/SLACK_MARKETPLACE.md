# Slack Marketplace Readiness

ReservationBot includes a Slack app manifest at `docs/slack-app-manifest.json`. Replace every `https://reservationbot.example.com` URL with the deployed API Gateway custom domain before importing the manifest into Slack.

## Organization Readiness

The manifest enables `org_deploy_enabled` for Enterprise Grid deployment and keeps Socket Mode disabled because ReservationBot is designed for HTTPS ingress through API Gateway. Token rotation is enabled in both `oauth_config.token_management_enabled` and `settings.token_rotation_enabled`.

## Scope Rationale

- `commands`: receive `/reserve`, `/resources`, and `/reservationbot` slash commands.
- `chat:write`: post reservation confirmations, audit messages, reminders, and update existing schedule posts.
- `chat:write.public`: post public reminders or audit messages into public channels where the bot has not been explicitly invited.
- `chat:write.customize`: support tenant-customized bot display name and avatar for messages.
- `channels:read`: inspect public channel metadata and membership.
- `groups:read`: inspect private channel metadata when the bot has been added.
- `im:write`: open/direct-message users for private reservation reminders and user-targeted audit destinations.
- `team:read`: call `team.info` for workspace metadata and workspace timezone fallback.
- `usergroups:read`: resolve Slack user groups used in role grants.
- `users:read`: resolve users, Slack handles, owners/admin metadata, and user timezones for reminders.

The manifest intentionally avoids history, file, admin, and write-management scopes until the product implements features that require them. That keeps the Marketplace review surface smaller and matches Slack's least-privilege expectations.

## Marketplace Submission Notes

- Add production privacy policy, terms of service, support, and security contact URLs in Slack's app distribution settings.
- Ensure the deployed request URLs use HTTPS and pass Slack signature verification.
- Verify all slash command URLs point to the same ingress handler or route-specific adapters.
- Confirm the app handles Enterprise Grid tenant IDs consistently before enabling broad org deployment.
- If customer-specific alias slash commands are required, create them in the Slack app configuration before distribution or generate a customer-specific manifest variant.

