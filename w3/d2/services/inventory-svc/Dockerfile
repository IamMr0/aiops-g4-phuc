FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y iproute2 && rm -rf /var/lib/apt/lists/*
RUN pip install flask psycopg2-binary prometheus-client

COPY app.py .

EXPOSE 8080

CMD ["python", "app.py"]
