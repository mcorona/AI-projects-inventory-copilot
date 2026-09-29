#!/usr/bin/env python3
"""CDK app del copiloto de inventario, verificado con cdk-nag (AwsSolutions).

    npx cdk synth                                     # sintetiza los 3 stacks + cdk-nag
    npx cdk deploy InventoryGuardrail                 # solo el guardrail (sin costo fijo)
    npx cdk deploy --all -c budgetEmail=tu@correo     # todo (Aurora y Lambda generan costo)
    npx cdk destroy --all -c allowDestroy=true
"""
import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from stacks.app_stack import AppStack
from stacks.data_stack import DataStack
from stacks.guardrail_stack import GuardrailStack
from stacks.nag import apply_suppressions

app = cdk.App()
env = cdk.Environment(account=app.node.try_get_context("account"), region=app.node.try_get_context("region") or "us-east-1")
allow_destroy = str(app.node.try_get_context("allowDestroy")).lower() == "true"

# sin assets: se despliega sin `cdk bootstrap` (no crea bucket, ECR ni roles de despliegue en la cuenta)
guardrail = GuardrailStack(app, "InventoryGuardrail", env=env, synthesizer=cdk.LegacyStackSynthesizer())
data = DataStack(app, "InventoryData", env=env, allow_destroy=allow_destroy,
                 engine=app.node.try_get_context("dbEngine") or "aurora")
api = AppStack(app, "InventoryApp", env=env, data=data, guardrail_arn=guardrail.guardrail_arn,
               guardrail_id=guardrail.guardrail_id, guardrail_version=guardrail.guardrail_version,
               guardrail_profile_arn=guardrail.guardrail_profile_arn,
               budget_email=app.node.try_get_context("budgetEmail"))
for stack in (guardrail, data, api):
    cdk.Tags.of(stack).add("project", "inventory-copilot")
    cdk.Tags.of(stack).add("data-classification", "synthetic")

apply_suppressions(data, api)
cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
app.synth()
