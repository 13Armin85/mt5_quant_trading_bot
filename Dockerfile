FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --requirement requirements.txt
COPY . .
RUN useradd --uid 10001 --create-home trader \
    && mkdir -p /app/data /app/control /app/logs /app/reports \
    && chown -R trader:trader /app
USER trader
ENTRYPOINT ["python", "-m", "docker_runtime.entrypoint"]
CMD ["run", "--mode", "live"]
