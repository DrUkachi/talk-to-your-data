FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-default-groups

ENV PATH="/app/.venv/bin:$PATH"

# matplotlib (pulled in transitively by pandasai, used by followup_agent) builds
# its font cache on first import -- confirmed by timing it (~32s) in a fresh
# container, not assumed. Every service imports followup_agent (slackbot,
# eda-api), so without this they'd all pay that cold-start cost silently on
# first request/connection. Baking it into the image layer here means it's
# paid once at build time instead of once per container start.
RUN python -c "import matplotlib.pyplot"

CMD ["uvicorn", "talk_to_your_data.agents.cleaning.api:app", "--host", "0.0.0.0", "--port", "8001"]
