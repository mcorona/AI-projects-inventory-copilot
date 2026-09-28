# Imagen de la Lambda del copiloto (API y bootstrap comparten imagen; cambia el handler)
FROM public.ecr.aws/lambda/python:3.13
COPY requirements.txt requirements-lambda.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r requirements.txt -r requirements-lambda.txt
COPY src ${LAMBDA_TASK_ROOT}/src
COPY scripts ${LAMBDA_TASK_ROOT}/scripts
COPY db ${LAMBDA_TASK_ROOT}/db
COPY data/docs ${LAMBDA_TASK_ROOT}/data/docs
COPY config ${LAMBDA_TASK_ROOT}/config
CMD ["src.api.lambda_handler.handler"]
