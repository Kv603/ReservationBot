ReservationBot

A robust, multi-tenant Slack app for reserving of shared resources.   Each tenant can customize the bot name and avatar, slash commands, message payloads, etc. 

Slack workspaces can fully customize their bot, their reservable resources, and their reservation policy.

A "shared resource" can be nearly anything.   Resources are assigned to one specific slack channel, and the "channel managers" (if any) have full control over adding, editing, or deleting shared resources assigned to their channel,
including reservations for those resources. Your slack admins have full control and can delegate role-based control to specific slack users and/or slack "user groups". 

Some resources might be fungible (like a parking space, or a seat in a conference room), 
others specific (the 3d-printer) or even encompass other resources 
(that entire conference room, when reserved nobody can reserve individual seats during the reserved time).    A reservable resource can be fungible (e.g. reserve one of the 5 available parking spaces, but not specify which space) 
or specific (a room has two tables, and the person making the reservation can specify the left table or the right table).

Admins/owners may set global limits for how far into the future a resource reservation can be created, 
maximum duration of a reservation, and which workspace members and "User Groups" can create resources, 
create reservations, manage other user's reservations (slack admins always have full access)

You can set resource reminder schedules per channel or per resources, including advance notice to each user of their upcoming reservation (configurable as to how far in advance to send the DM)
Public reminders/schedules for reserved resources can be scheduled to be sent as a public bot message in the channel at a specific time, and schedule can be daily, weekly, or monthly showing the current dau/week/month's reservation and/or the following days/weeks/months reservations.   Public reminders can display the full name and/or slack handle of the slack user who holds the reservation or be set to omit user names.
