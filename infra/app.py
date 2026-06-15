#!/usr/bin/env python3
import aws_cdk as cdk

from reservation_bot_stack import ReservationBotStack


app = cdk.App()

stage_name = app.node.try_get_context("stageName") or "dev"

ReservationBotStack(
    app,
    f"ReservationBot-{stage_name}",
    stage_name=stage_name,
    slack_signing_secret_arn=app.node.try_get_context("slackSigningSecretArn"),
    slack_bot_token_secret_arn=app.node.try_get_context("slackBotTokenSecretArn"),
)

app.synth()

