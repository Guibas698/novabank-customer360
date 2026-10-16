FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e . fastapi uvicorn[standard] httpx

ENV PYTHONPATH=/app/src
ENV CUSTOMER360_DATA_DIR=/app/data

EXPOSE 8000

CMD ["uvicorn", "customer360.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
