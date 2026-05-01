FROM public.ecr.aws/lambda/python:3.11

WORKDIR ${LAMBDA_TASK_ROOT}

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "google-genai>=1.0.0" \
    "httpx>=0.27.0" \
    "boto3>=1.35.0" \
    "aws-lambda-powertools>=3.0.0"

COPY src/agent/ ./agent/
COPY config/ ./config/

# Default command; SAM template overrides via ImageConfig.Command per function.
CMD ["agent.webhook_handler.handler"]
