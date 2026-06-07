FROM python:3.11-slim

WORKDIR /app

COPY lantern/ /app/lantern/
COPY bin/ /app/bin/

RUN chmod +x /app/bin/lantern-query

ENV PYTHONPATH=/app

# No pip install — collectors use stdlib only
