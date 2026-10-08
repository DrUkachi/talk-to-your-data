FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-default-groups

ENV PATH="/app/.venv/bin:$PATH"

CMD ["uvicorn", "talk_to_your_data.agents.cleaning.api:app", "--host", "0.0.0.0", "--port", "8001"]
