FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0
COPY . .
EXPOSE 8000
# gthread workers keep streaming (SSE) responses open without blocking other users
CMD ["sh", "-c", "gunicorn -w 2 -k gthread --threads 8 --timeout 300 -b 0.0.0.0:${PORT:-8000} app:app"]
