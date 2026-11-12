FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --system --uid 10001 --create-home gatherly && mkdir /data && chown gatherly /data
COPY gatherly/ gatherly/
COPY static/ static/
VOLUME ["/data"]
USER gatherly
ENV GATHERLY_PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=7s --start-period=20s --retries=3 CMD curl --fail --silent --max-time 6 http://127.0.0.1:8080/ready || exit 1
CMD ["uvicorn", "gatherly.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
