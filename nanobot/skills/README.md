# nanobot Skills

This directory contains built-in skills that extend nanobot's capabilities.

## Skill Format

Each skill is a directory containing a `SKILL.md` file with:
- YAML frontmatter (name, description, metadata)
- Markdown instructions for the agent

## Attribution

These skills are adapted from [OpenClaw](https://github.com/openclaw/openclaw)'s skill system.
The skill format and metadata structure follow OpenClaw's conventions to maintain compatibility.

## Available Skills

| Skill | Description |
|-------|-------------|
| `github` | Interact with GitHub using the `gh` CLI |
| `weather` | Get weather info using wttr.in and Open-Meteo |
| `summarize` | Summarize URLs, files, and YouTube videos |
| `tmux` | Remote-control tmux sessions |
| `skill-creator` | Create new skills |
| `code-review-expert` | Expert code review: SOLID, security, performance, error handling (from [sanyuan0704/code-review-expert](https://github.com/sanyuan0704/code-review-expert)) |
| `self-improving-agent` | Lifelong learning from skill use: semantic/episodic memory, pattern extraction, skill updates with evolution markers ([discussion](https://medium.com/@nomannayeem/lets-build-a-self-improving-ai-agent-that-learns-from-your-feedback-722d2ce9c2d9)); `LICENSE` in skill dir; optional Claude Code hooks (`hooks/*.sh`, needs bash) |