# AGENTS.md

Project guidance for Codex CLI (and any other agent) lives in [CLAUDE.md](CLAUDE.md)
and [ROADMAP.md](ROADMAP.md) -- read both before making changes. Key rules: plan and get
approval before implementing a phase, `uv` for everything, `ruff` + `mypy` clean, and all
LLM access goes through `src/talk_to_your_data/llm.py`.
