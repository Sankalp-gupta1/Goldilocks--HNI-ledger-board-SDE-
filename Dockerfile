FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=production HNI_DATA_DIR=/var/data HOST=0.0.0.0 PORT=8082 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY hni_accounting_system.py hni_ledger_extension.py generate_dataset.py wsgi.py ./
COPY goldilocks/ ./goldilocks/
COPY samples/ ./samples/
COPY scripts/ ./scripts/
COPY --from=frontend /build/web ./web/
RUN useradd --system --uid 10001 --create-home ledger && mkdir -p /var/data && chown ledger:ledger /var/data
USER ledger
EXPOSE 8082
CMD ["python", "hni_accounting_system.py"]
