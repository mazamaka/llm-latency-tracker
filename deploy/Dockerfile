# Probe container: deployed to a VPS in each region. Lightweight, minimal dependencies.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# REGION is set when the container starts in a given location (env); see docker-compose.yml
ENV REGION=unknown \
    INTERVAL=300 \
    DB_PATH=/data/measurements.db

# Unprivileged user (not root) + ownership of the volume directory
RUN useradd --create-home --uid 10001 probe && mkdir -p /data && chown probe /data
USER probe

VOLUME ["/data"]

# Continuous probe loop + site rebuild. For a production cron, swap for `python3 run.py`.
CMD ["python3", "schedule.py"]
