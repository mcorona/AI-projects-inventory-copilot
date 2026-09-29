"""Aplicacion: Lambda (imagen de contenedor) con el agente + API HTTP con autenticacion IAM.

- IAM de minimo privilegio: InvokeModel solo sobre los modelos usados y ApplyGuardrail solo sobre
  el guardrail del copiloto; lectura solo de los secretos que usa.
- Roles de DB separados (ro / po / approver / audit) como en local: cada uno con su secreto.
- Observabilidad: logs, metricas EMF de la telemetria del agente, alarmas y tablero.
- Concurrencia reservada baja como tope de costo.
"""
from __future__ import annotations

import json
from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

from stacks.data_stack import DB_PORT, DataStack

REPO_ROOT = Path(__file__).resolve().parents[2]
# ADR-010: MiniMax M2.1 on-demand es la mejor relacion calidad/costo del agente (100% en tools,
# exactitud y faithfulness; $0.0012 por consulta). No usa perfil de inferencia: solo la region local.
CHAT_MODEL = "minimax.minimax-m2.1"
EMBED_MODEL = "amazon.titan-embed-text-v2:0"
METRICS_NAMESPACE = "InventoryCopilot"
# regiones de EE. UU. a las que puede enrutar el perfil de guardrail us.guardrail.v1:0
GUARDRAIL_REGIONS = ("us-east-1", "us-east-2", "us-west-1", "us-west-2")


class AppStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, data: DataStack, guardrail_arn: str,
                 guardrail_id: str, guardrail_version: str, guardrail_profile_arn: str | None = None,
                 budget_email: str | None = None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        role_secrets = data.role_secrets
        signing_key = sm.Secret(self, "SigningKey", description="Firma HMAC de acciones pendientes de confirmacion")

        lambda_sg = data.app_sg

        env = {
            "LLM_PROVIDER": "bedrock", "BEDROCK_CHAT_MODEL": CHAT_MODEL,
            "EMBED_PROVIDER": "bedrock", "BEDROCK_EMBED_MODEL": EMBED_MODEL,
            "BEDROCK_GUARDRAIL_ID": guardrail_id, "BEDROCK_GUARDRAIL_VERSION": guardrail_version,
            "DB_HOST": data.db_host, "DB_PORT": str(DB_PORT), "DB_NAME": "inventory",
            "DB_ROLE_SECRETS": json.dumps({r: s.secret_arn for r, s in role_secrets.items()}),
            "DB_ADMIN_SECRET_ARN": data.db_secret.secret_arn,
            "SIGNING_KEY_SECRET_ARN": signing_key.secret_arn,
            "METRICS_NAMESPACE": METRICS_NAMESPACE,
        }
        image = dict(directory=str(REPO_ROOT), file="infra/lambda/Dockerfile",
                     exclude=[".venv", ".git", "infra/node_modules", "infra/cdk.out", "evals/results", "evals/reports",
                              "data/*.csv", "**/__pycache__"])
        def fn_role(name: str, log_group: logs.LogGroup) -> iam.Role:
            """Rol propio en vez de AWSLambdaBasicExecutionRole / VPCAccessExecutionRole (cdk-nag IAM4)."""
            role = iam.Role(self, f"{name}Role", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
            role.add_to_policy(iam.PolicyStatement(actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                                                   resources=[log_group.log_group_arn]))
            # interfaces de red en la VPC y trazas X-Ray: AWS no admite permisos a nivel de recurso
            role.add_to_policy(iam.PolicyStatement(
                actions=["ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DeleteNetworkInterface",
                         "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses",
                         "xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                resources=["*"]))
            return role

        common = dict(vpc=data.vpc, vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
                      security_groups=[lambda_sg], environment=env, architecture=lambda_.Architecture.ARM_64,
                      tracing=lambda_.Tracing.ACTIVE)

        api_logs = logs.LogGroup(self, "ApiFnLogs", retention=logs.RetentionDays.ONE_MONTH,
                                 removal_policy=RemovalPolicy.DESTROY)
        self.api_fn = lambda_.DockerImageFunction(
            self, "ApiFn", code=lambda_.DockerImageCode.from_image_asset(**image),
            memory_size=1024, timeout=Duration.seconds(60), reserved_concurrent_executions=5,
            log_group=api_logs, role=fn_role("ApiFn", api_logs), **common)
        bootstrap_logs = logs.LogGroup(self, "BootstrapFnLogs", retention=logs.RetentionDays.ONE_MONTH,
                                       removal_policy=RemovalPolicy.DESTROY)
        self.bootstrap_fn = lambda_.DockerImageFunction(
            self, "BootstrapFn",
            code=lambda_.DockerImageCode.from_image_asset(**image, cmd=["src.api.bootstrap.handler"]),
            memory_size=1024, timeout=Duration.minutes(10), log_group=bootstrap_logs,
            role=fn_role("BootstrapFn", bootstrap_logs), **common)

        # --- IAM de minimo privilegio
        # solo los dos modelos que usa la app, en la region del stack (sin perfiles entre regiones)
        model_arns = [f"arn:aws:bedrock:{self.region}::foundation-model/{m}" for m in (CHAT_MODEL, EMBED_MODEL)]
        for fn in (self.api_fn, self.bootstrap_fn):
            fn.add_to_role_policy(iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=model_arns))
            for s in role_secrets.values():
                s.grant_read(fn)
        # Tier STANDARD: el guardrail se evalua entre regiones via el perfil us.guardrail.v1:0, y IAM
        # exige el guardrail y el perfil en cada region de destino (hallado en el despliegue real: el
        # simulador aprobaba us-east-1 pero la llamada se negaba). Mismos recursos, sin comodines.
        guardrail_resources = [guardrail_arn]
        if guardrail_profile_arn:
            # guardrail_arn (export de la region de origen) se conserva para no romper la referencia entre
            # stacks; las otras regiones se arman desde el id, que CDK interpola (el ARN es un token)
            guardrail_resources = [guardrail_arn, guardrail_profile_arn] + [
                arn for r in GUARDRAIL_REGIONS if r != self.region for arn in (
                    f"arn:aws:bedrock:{r}:{self.account}:guardrail/{guardrail_id}",
                    f"arn:aws:bedrock:{r}:{self.account}:guardrail-profile/us.guardrail.v1:0")]
        self.api_fn.add_to_role_policy(iam.PolicyStatement(actions=["bedrock:ApplyGuardrail"],
                                                           resources=guardrail_resources))
        signing_key.grant_read(self.api_fn)
        data.db_secret.grant_read(self.bootstrap_fn)   # solo el bootstrap usa el admin

        # --- API HTTP con autenticacion IAM (SigV4) y logs de acceso
        self.api = apigw.HttpApi(self, "HttpApi", description="Inventory Copilot API",
                                 default_authorizer=authorizers.HttpIamAuthorizer(),
                                 default_integration=integrations.HttpLambdaIntegration("ApiIntegration", self.api_fn))
        access_logs = logs.LogGroup(self, "ApiAccessLogs", retention=logs.RetentionDays.ONE_MONTH,
                                    removal_policy=RemovalPolicy.DESTROY)
        stage = self.api.default_stage.node.default_child
        stage.access_log_settings = apigw.CfnStage.AccessLogSettingsProperty(
            destination_arn=access_logs.log_group_arn,
            format=json.dumps({"requestId": "$context.requestId", "caller": "$context.identity.caller",
                               "route": "$context.routeKey", "status": "$context.status",
                               "latencyMs": "$context.responseLatency"}))
        stage.default_route_settings = apigw.CfnStage.RouteSettingsProperty(
            throttling_burst_limit=10, throttling_rate_limit=5)

        # --- observabilidad: errores, latencia y costo por consulta (EMF emitido por la Lambda)
        errors = cw.Alarm(self, "ApiErrors", metric=self.api_fn.metric_errors(period=Duration.minutes(5)),
                          threshold=1, evaluation_periods=1, alarm_description="Errores en la Lambda del agente")
        p95 = cw.Alarm(self, "ApiLatencyP95", metric=self.api_fn.metric_duration(statistic="p95",
                                                                                 period=Duration.minutes(5)),
                       threshold=30_000, evaluation_periods=3, alarm_description="Latencia p95 > 30 s")
        custom = lambda name, stat="Average": cw.Metric(namespace=METRICS_NAMESPACE, metric_name=name,  # noqa: E731
                                                        statistic=stat, period=Duration.minutes(5))
        dashboard = cw.Dashboard(self, "Dashboard", dashboard_name="inventory-copilot")
        dashboard.add_widgets(
            cw.GraphWidget(title="Latencia del agente (ms)", left=[custom("LatencyMs", "p50"), custom("LatencyMs", "p95")]),
            cw.GraphWidget(title="Costo Bedrock por consulta (USD)", left=[custom("CostUsd")]),
            cw.GraphWidget(title="Tokens por consulta", left=[custom("InputTokens"), custom("OutputTokens")]),
            cw.AlarmStatusWidget(title="Alarmas", alarms=[errors, p95]))

        if budget_email:
            budgets.CfnBudget(self, "MonthlyBudget", budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST", time_unit="MONTHLY", budget_limit=budgets.CfnBudget.SpendProperty(amount=20, unit="USD")),
                notifications_with_subscribers=[budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN", notification_type="ACTUAL", threshold=80),
                    subscribers=[budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL",
                                                                      address=budget_email)])])

        CfnOutput(self, "ApiUrl", value=self.api.api_endpoint)
        CfnOutput(self, "BootstrapFunction", value=self.bootstrap_fn.function_name)
