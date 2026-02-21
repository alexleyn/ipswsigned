FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY bot.py database.py ipsw_api.py checker.py ./

# SQLite DB lives in a volume so it survives container restarts
VOLUME ["/data"]
ENV DB_PATH=/data/ipsw_bot.db

CMD ["python", "bot.py"]
