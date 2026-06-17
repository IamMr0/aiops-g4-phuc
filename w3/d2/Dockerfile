FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY apps /app/apps
EXPOSE 8080 8000

CMD ["uvicorn", "apps.service_app:app", "--host", "0.0.0.0", "--port", "8080"]