FROM python:3.10-slim

LABEL maintainer="NMS Web Main"
LABEL description="Network Monitoring System — Web Main (Flask + Waitress)"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps: libpq for psycopg2, libgl for any PIL/reportlab image ops
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    libgl1 \
    libglib2.0-0 \
    gcc \
    curl \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer cache — only invalidated when requirements change)
COPY requirements.server.txt /app/requirements.server.txt
RUN pip install --no-cache-dir -r requirements.server.txt

# Copy only the application source (no installers, EXEs, ZIPs, node_modules)
COPY app.py config.py extensions.py web_main.py /app/
COPY routes/         /app/routes/
COPY services/       /app/services/
COPY models/         /app/models/
COPY middleware/     /app/middleware/
COPY workers/        /app/workers/
COPY utils/          /app/utils/
COPY metrics/        /app/metrics/
COPY events/         /app/events/
COPY client_modules/ /app/client_modules/
COPY file_transfer/  /app/file_transfer/
COPY thresholds/     /app/thresholds/
COPY templates/      /app/templates/
COPY static/         /app/static/

# Create directories for runtime data
RUN mkdir -p /app/client_uploads /app/events /app/instance

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:5001/health || exit 1

CMD ["python", "web_main.py"]
