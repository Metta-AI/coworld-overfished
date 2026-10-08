FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY viewer ./viewer
RUN pip install --no-cache-dir uv==0.11.7 && uv sync --frozen --no-dev --no-editable
ENV PATH="/app/.venv/bin:$PATH"

ENV OVERFISHED_VIEWER_DIR=/app/viewer \
    COGAME_HOST=0.0.0.0 \
    COGAME_PORT=8080 \
    PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["overfished"]
