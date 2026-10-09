# Codex CLI + GPT-6.1-Sol

The app and Codex CLI share one model deployment and one set of credentials.

| | App (`src/talk_to_your_data/llm.py`) | Codex CLI (`~/.codex/config.toml`) |
|---|---|---|
| Key | `OPENAI_API_KEY` | `env_key = "OPENAI_API_KEY"` |
| Endpoint | `OPENAI_BASE_URL` | `base_url` (literal copy of the same URL) |
| Model | `OPENAI_MODEL` (default `gpt-6.1-sol`) | `model = "gpt-6.1-sol"` |

Setup: `npm install -g @openai/codex`, fill `OPENAI_API_KEY`/`OPENAI_BASE_URL` in `.env`,
copy the URL into `base_url` in `~/.codex/config.toml`, then
`set -a; . ./.env; set +a; codex` from the project root.

Both use the Responses API (`gpt-6.1-sol` rejects function tools on Chat Completions).
