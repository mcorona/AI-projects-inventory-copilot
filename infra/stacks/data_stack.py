"""Datos: VPC aislada (sin NAT) + PostgreSQL con pgvector (Aurora Serverless v2 o RDS).

- Subredes PRIVATE_ISOLATED: sin salida a internet; Bedrock, Secrets Manager y CloudWatch Logs
  por VPC endpoints (menos superficie y sin costo de NAT).
- Aurora Serverless v2 con auto-pause (0 ACU): el costo en reposo es solo almacenamiento.
- Credenciales en Secrets Manager con rotacion; cifrado en reposo; autenticacion IAM habilitada.
Equivale en AWS al Postgres + pgvector de docker-compose.yml. Con -c dbEngine=rds se usa RDS
PostgreSQL db.t4g.micro, compatible con el plan gratuito de AWS (misma red, seguridad y rotacion).
"""
from __future__ import annotations

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from constructs import Construct

DB_PORT = 5438   # puerto no estandar (cdk-nag AwsSolutions-RDS11)
VPC_CIDR = "10.20.0.0/16"
CLUSTER_ID = "inventory-copilot"
DB_ROLES = ("copilot_ro", "copilot_po", "copilot_approver", "copilot_audit")


class DataStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, allow_destroy: bool = False,
                 engine: str = "aurora", **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.allow_destroy = allow_destroy

        self.vpc = ec2.Vpc(
            self, "Vpc", max_azs=2, nat_gateways=0, ip_addresses=ec2.IpAddresses.cidr(VPC_CIDR),
            subnet_configuration=[ec2.SubnetConfiguration(name="isolated", cidr_mask=24,
                                                          subnet_type=ec2.SubnetType.PRIVATE_ISOLATED)],
            flow_logs={"all": ec2.FlowLogOptions(destination=ec2.FlowLogDestination.to_cloud_watch_logs(
                logs.LogGroup(self, "VpcFlowLogs", retention=logs.RetentionDays.ONE_MONTH,
                              removal_policy=RemovalPolicy.DESTROY)))})
        self.endpoint_sg = ec2.SecurityGroup(self, "EndpointSg", vpc=self.vpc, allow_all_outbound=False,
                                             description="VPC endpoints (HTTPS desde la VPC)")
        self.endpoint_sg.add_ingress_rule(ec2.Peer.ipv4(VPC_CIDR), ec2.Port.tcp(443),
                                          "HTTPS desde la VPC")
        endpoints = {}
        for name, service in (("BedrockRuntime", ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME),
                              ("SecretsManager", ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER),
                              ("Logs", ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS)):
            # open=False: la regla de ingreso ya la define endpoint_sg con el CIDR literal
            endpoints[name] = self.vpc.add_interface_endpoint(name, service=service, open=False,
                                                              security_groups=[self.endpoint_sg],
                                                              private_dns_enabled=True)

        self.db_sg = ec2.SecurityGroup(self, "DbSg", vpc=self.vpc, allow_all_outbound=False,
                                       description="PostgreSQL (Aurora o RDS)")
        # el SG de las Lambdas vive aqui para que la regla de ingreso no cree un ciclo entre stacks
        self.app_sg = ec2.SecurityGroup(self, "AppSg", vpc=self.vpc, description="Lambdas del copiloto")
        self.db_sg.add_ingress_rule(self.app_sg, ec2.Port.tcp(DB_PORT), "Lambdas del copiloto")
        # dbEngine=aurora (arquitectura objetivo) | rds (cuentas con el plan gratuito de AWS: exige
        # "express configuration" para Aurora, que CloudFormation no soporta y fija VPC/puerto/SG)
        self.engine = engine
        common = dict(vpc=self.vpc, vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
                      security_groups=[self.db_sg], port=DB_PORT,
                      credentials=rds.Credentials.from_generated_secret("postgres"),
                      storage_encrypted=True, iam_authentication=True,
                      # 7 dias en modo normal; 1 dia en el despliegue temporal (limite del plan gratuito)
                      backup_retention=Duration.days(1 if allow_destroy else 7),
                      cloudwatch_logs_exports=["postgresql"],
                      deletion_protection=not allow_destroy,
                      removal_policy=RemovalPolicy.DESTROY if allow_destroy else RemovalPolicy.SNAPSHOT)
        if engine == "aurora":
            # log group creado aqui con retencion (evita la Lambda LogRetention que CDK agregaria)
            db_logs = logs.LogGroup(self, "AuroraLogs", log_group_name=f"/aws/rds/cluster/{CLUSTER_ID}/postgresql",
                                    retention=logs.RetentionDays.ONE_MONTH, removal_policy=RemovalPolicy.DESTROY)
            retention = common.pop("backup_retention")
            self.db = rds.DatabaseCluster(
                self, "Aurora", cluster_identifier=CLUSTER_ID,
                engine=rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_16_9),
                writer=rds.ClusterInstance.serverless_v2("writer"),
                serverless_v2_min_capacity=0, serverless_v2_max_capacity=2,
                serverless_v2_auto_pause_duration=Duration.minutes(10),
                default_database_name="inventory", backup=rds.BackupProps(retention=retention), **common)
            self.db_host = self.db.cluster_endpoint.hostname
        elif engine == "rds":
            db_logs = logs.LogGroup(self, "RdsLogs", log_group_name=f"/aws/rds/instance/{CLUSTER_ID}/postgresql",
                                    retention=logs.RetentionDays.ONE_MONTH, removal_policy=RemovalPolicy.DESTROY)
            self.db = rds.DatabaseInstance(
                self, "Database", instance_identifier=CLUSTER_ID,
                engine=rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_16_9),
                instance_type=ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO),
                allocated_storage=20, max_allocated_storage=20, multi_az=False,
                database_name="inventory", **common)
            self.db_host = self.db.db_instance_endpoint_address
        else:
            raise ValueError(f"dbEngine invalido: {engine} (aurora | rds)")
        self.db.node.add_dependency(db_logs)
        self.db_secret = self.db.secret
        self.db.add_rotation_single_user(automatically_after=Duration.days(30),
                                         vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
                                         endpoint=endpoints["SecretsManager"])

        # Un secreto por rol de minimo privilegio (ro / po / approver / audit), rotado cada 30 dias
        # con la estrategia multiusuario (el secreto maestro rota a los demas; sin cortes de servicio)
        self.role_secrets = {}
        for role in DB_ROLES:
            secret = rds.DatabaseSecret(self, f"Secret-{role}", username=role,
                                        master_secret=self.db.secret, exclude_characters=" %+~`#$&*()|[]{}:;<>?!'/@\"\\")
            attached = secret.attach(self.db)
            self.db.add_rotation_multi_user(f"Rotate-{role}", secret=attached,
                                                 automatically_after=Duration.days(30),
                                                 vpc_subnets=ec2.SubnetSelection(
                                                     subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
                                                 endpoint=endpoints["SecretsManager"])
            self.role_secrets[role] = attached
        CfnOutput(self, "DbEndpoint", value=self.db_host)
