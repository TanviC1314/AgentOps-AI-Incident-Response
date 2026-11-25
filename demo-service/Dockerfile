FROM python:3.11-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV APP_VERSION=1.0.0 PYTHONUNBUFFERED=1
EXPOSE 8001
USER 10001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
