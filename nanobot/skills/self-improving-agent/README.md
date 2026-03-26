# Self-Improving Agent（nanobot builtin）

本目录为 **nanobot** 内置技能：从任务中提炼模式，更新 `memory/` 与相关 `SKILL.md`。

## 与 Cursor / `.agents/skills` 副本的关系

若你本机另有 `~/.agents/skills/self-improving-agent`，二者可独立演进；以本仓库 `nanobot/skills/self-improving-agent` 为准参与 nanobot 加载。

## 目录

- `SKILL.md` — 主流程（nanobot 工具名、手动触发说明）
- `memory/semantic-patterns.json` — 语义模式库（`target_skills` 仅指向本仓库 builtin / 工作区技能名）
- `memory/episodic/` — 情景记录（按需新建 `YYYY/` 与 json）
- `memory/working/` — 当前会话/错误上下文
- `templates/` — 模式、纠错、校验模板
- `references/appendix.md` — 附录与校验说明
- `hooks/` — 供 **Claude Code** 等环境可选挂载；nanobot 默认不执行；Windows 需 Git Bash / WSL 等可运行 `.sh` 的环境

## License

见本目录 `LICENSE`（MIT）。
