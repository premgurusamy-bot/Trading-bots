# Optional: run the ERP on a cloud server (Render, Railway, Fly.io, a VPS...) so the phone app works
# anywhere without your PC. Mount a persistent disk at /app/data and set ERP_PASSWORD and your keys as env vars.
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV APP_HOST=0.0.0.0 APP_PORT=8100 ERP_DB_PATH=/app/data/erp.db
EXPOSE 8100
CMD ["python", "-m", "app.main"]
