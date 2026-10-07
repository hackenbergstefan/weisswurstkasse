FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM python:3.13-slim
COPY --from=uv /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 AUDIT_LOG_FILE=/data/audit.log
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
RUN groupadd --gid 10001 breakfast \
    && useradd --uid 10001 --gid breakfast --create-home breakfast \
    && mkdir /data && chown breakfast:breakfast /data
RUN SECRET_KEY=build-only-static-assets DEBUG=true uv run python manage.py collectstatic --noinput \
    && chown breakfast:breakfast /data/audit.log
USER breakfast
EXPOSE 8000
ENTRYPOINT ["/app/.venv/bin/python", "/app/docker_entrypoint.py"]
CMD ["web"]
