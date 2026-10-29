# Stage 1: install dependencies into a virtualenv
FROM python:3.11-slim AS build

WORKDIR /usr/src/app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY src/ .

# Stage 2: runtime image
FROM python:3.11-slim

WORKDIR /usr/src/app

COPY --from=build /opt/venv /opt/venv
COPY --from=build /usr/src/app .

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /usr/src/app
USER appuser

ENV PORT=8080
EXPOSE $PORT

# Configuration comes from environment variables (Cloud Run / Fly secrets),
# not a baked-in .env file. The platform may choose the port via $PORT;
# exec makes uvicorn PID 1 so it receives shutdown signals.
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
