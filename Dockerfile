FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS test

COPY requirements-test.txt .
RUN pip install --no-cache-dir -r requirements-test.txt

COPY . .
RUN chmod +x scripts/railway_payment_agent_start.sh

CMD ["./scripts/railway_payment_agent_start.sh"]

FROM base AS runtime

# Screenshot extraction requires OCR in the operations-intelligence service.
# This is installed in the shared image but does not change the payment agent entrypoint.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr && rm -rf /var/lib/apt/lists/*

COPY . .
RUN chmod +x scripts/railway_payment_agent_start.sh

CMD ["./scripts/railway_payment_agent_start.sh"]
