"""Entrada de la Lambda: carga configuracion desde Secrets Manager y sirve la API con Mangum."""
from mangum import Mangum

from src.api.aws_runtime import load_runtime_env

load_runtime_env()

from src.api.app import app  # noqa: E402

handler = Mangum(app, lifespan="off")
