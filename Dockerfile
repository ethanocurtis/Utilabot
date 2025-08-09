FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py /app/bot.py

RUN mkdir -p /app/data
ENV DATA_PATH=/app/data/db.json

CMD ["python", "-u", "bot.py"]
