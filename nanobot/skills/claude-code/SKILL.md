---
name: claude-code
description: Delegate coding tasks to Claude Code CLI for implementation
short_description: "Claude Code CLI coding agent"
keywords: "code, coding, implement, refactor, debug, claude, write code, programming"
category: "coding"
metadata: {"nanobot":{"emoji":"🤖","requires":{"bins":["claude"]}}}
---

# Claude Code Skill

Claude Code CLI is a specialized coding agent that excels at complex coding tasks.
Use it when you need substantial code implementation, refactoring, or debugging.

## When to Use

Use the `spawn` tool with `backend: "claude_code"` for:

- **Implementing new features** - Building features from scratch
- **Large-scale refactoring** - Restructuring codebases
- **Writing comprehensive tests** - Test suites and coverage
- **Debugging complex issues** - Finding and fixing bugs
- **Code review and improvements** - Quality enhancements

## When NOT to Use

For simple operations, prefer built-in tools:

- Reading files → use `read_file`
- Writing files → use `write_file`
- Editing files → use `edit_file`
- Running commands → use `exec`

## Usage

Use `spawn` with `backend: "claude_code"` (or `backend: "auto"` for coder templates):

1. Main agent spawns a subagent with coding task
2. Subagent runs via Claude Code CLI backend
3. Progress is streamed; result is announced when done

## Parameters (spawn tool)

| Parameter | Type   | Default | Description                                      |
| --------- | ------ | ------- | ------------------------------------------------ |
| task      | string | required| The coding task description                      |
| template  | string | "coder"| Use "coder" or "claude-coder" for Claude Code    |
| backend   | string | "auto"  | Set to "claude_code" to force Claude Code CLI    |

## Examples

### Basic Feature Implementation

```json
{
  "task": "Implement a REST API endpoint for user authentication with JWT tokens. Create the endpoint in api/auth.py with login, logout, and refresh token functionality.",
  "template": "coder",
  "backend": "claude_code"
}
```

### Large Refactoring

```json
{
  "task": "Refactor the entire test suite to use pytest instead of unittest. Update all test files and ensure 100% compatibility.",
  "template": "coder",
  "backend": "claude_code"
}
```

### Debugging Task

```json
{
  "task": "Debug the memory leak in the worker process. Start by analyzing the memory profile in profiler.py and identify the root cause.",
  "template": "coder",
  "backend": "claude_code"
}
```

## Notes

- Claude Code runs with its **own token budget** (separate from nanobot)
- Results are delivered **asynchronously** via system message
- Check the notification for detailed results
- Multiple tasks can run concurrently (up to configured limit)

## Requirements

- Claude Code CLI must be installed: `npm install -g @anthropic-ai/claude-code`
- ANTHROPIC_API_KEY environment variable must be set
