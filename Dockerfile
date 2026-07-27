FROM python:3.11-slim

# Install system dependencie s
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and scripts
COPY epg_gui.py epg_merge.py run_all.sh entrypoint.sh ./

# Set execution permissions
RUN chmod +x run_all.sh entrypoint.sh

# Configure Cron (Runs daily at 3:00 AM UTC)
RUN echo "0 3 * * * root /app/run_all.sh >> /app/cron.log 2>&1" > /etc/cron.d/epg-cron \
    && chmod 0644 /etc/cron.d/epg-cron \
    && crontab /etc/cron.d/epg-cron

EXPOSE 7860

ENTRYPOINT ["/app/entrypoint.sh"]
