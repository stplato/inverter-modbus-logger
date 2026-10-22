# Step 8 - ONE image for both the inverter simulator and the logger.
# docker-compose.yml starts it twice, with a different command each time.

FROM python:3.13-slim

# Print logs immediately (no buffering) and don't write .pyc files
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 1. Install the Python packages first: this layer is cached and only rebuilt
#    when requirements.txt changes (fast rebuilds when only code changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. Copy the project code
COPY common/    common/
COPY simulator/ simulator/
COPY logger/    logger/
COPY sql/       sql/
COPY tests/     tests/

# Default command (overridden per service in docker-compose.yml)
CMD ["python", "-m", "simulator.server", "--host", "0.0.0.0"]
