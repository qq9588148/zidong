FROM python:3.12.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/champion-follow

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

COPY server ./server
RUN python -m pip install --no-cache-dir "./server[test]"

COPY ops/migrations ./ops/migrations

WORKDIR /opt/champion-follow/server
CMD ["python", "-m", "uvicorn", "champion_follow_server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
