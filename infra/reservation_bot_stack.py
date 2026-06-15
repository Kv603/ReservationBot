from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as integrations,
    aws_cloudwatch as cloudwatch,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
    aws_sqs as sqs,
)
from constructs import Construct


class ReservationBotStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        stage_name: str,
        slack_signing_secret_arn: str | None,
        slack_bot_token_secret_arn: str | None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not slack_signing_secret_arn:
            raise ValueError("CDK context value slackSigningSecretArn is required")
        if not slack_bot_token_secret_arn:
            raise ValueError("CDK context value slackBotTokenSecretArn is required")

        table = dynamodb.Table(
            self,
            "ReservationTable",
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
        )
        table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(name="GSI1PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="GSI1SK", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        dead_letter_queue = sqs.Queue(
            self,
            "WorkerDeadLetterQueue",
            retention_period=Duration.days(14),
            enforce_ssl=True,
        )
        worker_queue = sqs.Queue(
            self,
            "WorkerQueue",
            visibility_timeout=Duration.seconds(90),
            retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=5, queue=dead_letter_queue),
            enforce_ssl=True,
        )

        slack_signing_secret = secretsmanager.Secret.from_secret_complete_arn(
            self,
            "SlackSigningSecret",
            slack_signing_secret_arn,
        )
        slack_bot_token_secret = secretsmanager.Secret.from_secret_complete_arn(
            self,
            "SlackBotTokenSecret",
            slack_bot_token_secret_arn,
        )

        lambda_path = Path(__file__).resolve().parent.parent / "api"
        handler = lambda_.Function(
            self,
            "SlackIngressHandler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handlers.slack_ingress.handler",
            code=lambda_.Code.from_asset(str(lambda_path)),
            timeout=Duration.seconds(10),
            memory_size=512,
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={
                "POWERTOOLS_SERVICE_NAME": "reservationbot",
                "POWERTOOLS_LOG_LEVEL": "INFO",
                "RESERVATION_TABLE_NAME": table.table_name,
                "WORKER_QUEUE_URL": worker_queue.queue_url,
                "SLACK_SIGNING_SECRET_ARN": slack_signing_secret.secret_arn,
                "SLACK_BOT_TOKEN_SECRET_ARN": slack_bot_token_secret.secret_arn,
                "STAGE_NAME": stage_name,
            },
        )

        table.grant_read_write_data(handler)
        worker_queue.grant_send_messages(handler)
        slack_signing_secret.grant_read(handler)
        slack_bot_token_secret.grant_read(handler)
        handler.add_to_role_policy(
            iam.PolicyStatement(
                actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                resources=["*"],
            )
        )

        api_logs = logs.LogGroup(
            self,
            "HttpApiAccessLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        api = apigwv2.HttpApi(
            self,
            "SlackHttpApi",
            api_name=f"reservationbot-{stage_name}",
            default_integration=integrations.HttpLambdaIntegration(
                "SlackLambdaIntegration",
                handler,
            ),
        )

        apigwv2.CfnStage(
            self,
            "DefaultStageAccessLogs",
            api_id=api.api_id,
            stage_name="$default",
            auto_deploy=True,
            access_log_settings=apigwv2.CfnStage.AccessLogSettingsProperty(
                destination_arn=api_logs.log_group_arn,
                format=(
                    '{"requestId":"$context.requestId","ip":"$context.identity.sourceIp",'
                    '"requestTime":"$context.requestTime","httpMethod":"$context.httpMethod",'
                    '"routeKey":"$context.routeKey","status":"$context.status",'
                    '"protocol":"$context.protocol","responseLength":"$context.responseLength"}'
                ),
            ),
        )
        api_logs.grant_write(iam.ServicePrincipal("apigateway.amazonaws.com"))

        cloudwatch.Alarm(
            self,
            "SlackIngressErrorsAlarm",
            metric=handler.metric_errors(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
        )
        cloudwatch.Alarm(
            self,
            "SlackIngressLatencyAlarm",
            metric=handler.metric_duration(period=Duration.minutes(5)),
            threshold=2500,
            evaluation_periods=3,
        )
        cloudwatch.Alarm(
            self,
            "WorkerDlqAlarm",
            metric=dead_letter_queue.metric_approximate_number_of_messages_visible(),
            threshold=1,
            evaluation_periods=1,
        )

        CfnOutput(self, "SlackRequestUrl", value=api.api_endpoint)
        CfnOutput(self, "ReservationTableName", value=table.table_name)
        CfnOutput(self, "WorkerQueueUrl", value=worker_queue.queue_url)
