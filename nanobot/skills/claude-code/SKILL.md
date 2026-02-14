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

Use `claude_code` tool for:

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

The `claude_code` tool handles the entire workflow:

1. Starts Claude Code in background
2. Monitors for completion via Hook
3. Notifies you when done

## Parameters

| Parameter       | Type    | Default   | Description                                                  |
| --------------- | ------- | --------- | ------------------------------------------------------------ |
| prompt          | string  | required  | The coding task description                                  |
| workdir         | string  | workspace | Target directory (must be within workspace, can be relative) |
| permission_mode | string  | "auto"    | Permission handling mode                                     |
| agent_teams     | boolean | false     | Enable parallel agent mode                                   |
| teammate_mode   | string  | "auto"    | Teammate coordination mode                                   |
| timeout         | integer | 600       | Task timeout in seconds                                      |

## Workspace Restriction

The `workdir` parameter has different rules based on path type:
- **Relative path** (`"chris-blog"`): Must be within workspace (recommended)
- **Absolute path** (`"D:/projects/myapp"`): Can be any location on the system

Use relative paths for workspace projects, absolute paths when you need to work outside the workspace.

## Permission Modes

| Mode              | Description                          |
| ----------------- | ------------------------------------ |
| default           | Ask for permissions interactively    |
| plan              | Plan first, then execute             |
| auto              | Auto-approve safe operations         |
| bypassPermissions | Skip all permissions (use carefully) |

## Examples

### Basic Feature Implementation

```json
{
  "prompt": "Implement a REST API endpoint for user authentication with JWT tokens. Create the endpoint in api/auth.py with login, logout, and refresh token functionality.",
  "workdir": "/path/to/project",
  "permission_mode": "auto"
}
```

### Large Refactoring with Agent Teams

```json
{
  "prompt": "Refactor the entire test suite to use pytest instead of unittest. Update all test files and ensure 100% compatibility.",
  "workdir": "/path/to/project",
  "permission_mode": "auto",
  "agent_teams": true,
  "teammate_mode": "auto"
}
```

### Debugging Task

```json
{
  "prompt": "Debug the memory leak in the worker process. Start by analyzing the memory profile in profiler.py and identify the root cause.",
  "workdir": "/path/to/project",
  "timeout": 1200
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
