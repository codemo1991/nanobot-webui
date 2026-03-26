# Appendix（nanobot builtin `self-improving-agent`）

内存文件**相对本技能目录** `nanobot/skills/self-improving-agent/`（或仓库内实际路径）。

## Self-Validation

### Validation Report Template

```markdown
## Validation Report Template

**Date**: [YYYY-MM-DD]
**Scope**: [skill(s) validated]

### Checks
- [ ] Examples compile or run
- [ ] Checklists match current repo conventions
- [ ] External references still valid
- [ ] No duplicated or conflicting guidance

### Findings
- [Finding 1]
- [Finding 2]

### Actions
- [Action 1]
- [Action 2]
```

## Memory File Structure（skill-local）

```
self-improving-agent/
├── memory/
│   ├── semantic-patterns.json
│   ├── episodic/
│   │   └── YYYY/
│   │       └── YYYY-MM-DD-topic.json
│   └── working/
│       ├── current_session.json
│       ├── last_error.json
│       └── session_end.json
```

## Claude Code / 外部编排（可选）

若使用 Claude Code 等工作流编排，可实现「技能结束后后台自我改进」；**nanobot 主循环默认不执行**，见根目录 `SKILL.md` 说明。

## Continuous Learning Metrics（示例）

```json
{
  "metrics": {
    "patterns_learned": 47,
    "patterns_applied": 238,
    "skills_updated": 12,
    "avg_confidence": 0.87,
    "self_corrections": 8
  }
}
```

## Human-in-the-Loop

### Feedback Collection

```markdown
## Self-Improvement Summary

I've learned from our session and updated:

### Updated Skills
- `debugger`: Added callback verification pattern
- `prd-planner`: Enhanced UI/UX specification requirements

### Patterns Extracted
1. **state_monitoring_over_callbacks**: Use usePrevious for state-driven side effects
2. **ui_ux_specification_granularity**: Explicit visual specs prevent rework

### Confidence Levels
- New patterns: 0.85 (needs validation)
- Reinforced patterns: 0.95 (well-established)

### Your Feedback
Rate these improvements (1-10):
- Were the updates helpful?
- Should I apply this pattern more broadly?
- Any corrections needed?
```

### Feedback Integration

```yaml
User Feedback:
  positive (rating >= 7):
    action: Increase pattern confidence
    scope: Expand to related skills

  neutral (rating 4-6):
    action: Keep pattern, gather more data
    scope: Current skill only

  negative (rating <= 3):
    action: Decrease confidence, revise pattern
    scope: Remove from active patterns
```

## Templates

| Template | Purpose |
|----------|---------|
| `templates/pattern-template.md` | Adding new patterns |
| `templates/correction-template.md` | Fixing incorrect guidance |
| `templates/validation-template.md` | Validating skill accuracy |

## References

- [SimpleMem](https://arxiv.org/html/2601.02553v1)
- [A Survey on the Memory Mechanism of Large Language Model Agents](https://dl.acm.org/doi/10.1145/3748302)
- [Lifelong Learning of LLM based Agents](https://arxiv.org/html/2501.07278v1)
- [Evo-Memory: DeepMind's Benchmark](https://shothota.medium.com/evo-memory-deepminds-new-benchmark)
- [Let's Build a Self-Improving AI Agent](https://medium.com/@nomannayeem/lets-build-a-self-improving-ai-agent-that-learns-from-your-feedback-722d2ce9c2d9)
