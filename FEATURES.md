ReservationBot

A robust, multi-tenant Slack app for reserving of shared resources.   Each tenant can customize the bot name and avatar, slash commands, message payloads, etc. 

Slack workspaces can fully customize their bot, their reservable resources, and their reservation policy.

A "shared resource" can be nearly anything.   Resources are assigned to one specific slack channel, and the "channel managers" (if any) have full control over adding, editing, or deleting shared resources assigned to their channel,
including reservations for those resources. Your slack admins have full control and can delegate role-based control to specific slack users and/or slack "user groups". 

Reservations may include an optional Reason, a freeform Slack markdown field that can include emojis.

Typing `/reserve` or any globally configured reservation alias in a channel with reservable resources opens a Slack dialog/modal. The user can select a resource, date, time, duration, and optional Reason. Users can also open a dialog to list their own future reservations and cancel one.

Some resources might be fungible (like a parking space, or a seat in a conference room), 
others specific (the 3d-printer) or even encompass other resources 
(that entire conference room, when reserved nobody can reserve individual seats during the reserved time).    A reservable resource can be fungible (e.g. reserve one of the 5 available parking spaces, but not specify which space) 
or specific (a room has two tables, and the person making the reservation can specify the left table or the right table).

Admins/owners may set global limits for how far into the future a resource reservation can be created, 
maximum duration of a reservation, and which workspace members and "User Groups" can create resources, 
create reservations, manage other user's reservations (slack admins always have full access)

Workspace owners and admins may configure global bot settings, including bot name, avatar, alias slash commands,
workspace timezone, audit destination, global minimum reservation duration, global maximum reservation duration,
and private reservation reminder defaults.

ReservationBot uses Slack `<!date>` formatting for date/time values embedded in messages. The fallback plaintext
is rendered in the workspace timezone from Slack `team.info`; direct reminders prefer each user's timezone when
available for user-local context.

Audit destinations can be channels or users. All changes affecting resources, reminders, settings, and reservations
are persisted and posted to audit with the acting Slack user mentioned. When the audit destination changes or is
removed, one final message is posted to the old destination.

You can set resource reminder schedules per channel or per resources, including advance notice to each user of their upcoming reservation (configurable as to how far in advance to send the DM)
Public reminders/schedules for reserved resources can be scheduled to be sent as a public bot message in the channel at a specific time, and schedule can be daily, weekly, or monthly showing the current dau/week/month's reservation and/or the following days/weeks/months reservations.   Public reminders can display the full name and/or slack handle of the slack user who holds the reservation or be set to omit user names.

Public reminder schedules support a title, 15-minute post-time granularity, daily or weekly cadence, and views for today, tomorrow, or the upcoming week. They can post to the resource channel, another Slack channel, or a Slack user. They can include all channel resources, a specific resource list, or admin-only EVERYTHING across the tenant. Posted reminder timestamps are saved for future Slack message updates.
