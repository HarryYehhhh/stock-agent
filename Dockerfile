FROM public.ecr.aws/lambda/python:3.11

WORKDIR ${LAMBDA_TASK_ROOT}

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "google-genai>=1.0.0" \
    "httpx>=0.27.0" \
    "aws-lambda-powertools>=3.0.0"

COPY src/agent/ ./agent/

CMD ["agent.scheduler_handler.handler"]
