# Calendar Skill Design

## Goal
Add a `calendar` skill to nanobot so that when a user says they want to record something for a specific day (e.g. "明天下午开会"), the agent automatically creates a calendar event. The event should be:
- **All-day event** (`is_all_day = true`)
- **No reminders** (`reminders = []`)

## Context
Nanobot already has a full calendar backend:
- SQLite repository: `nanobot/storage/calendar_repository.py` (stores events in `~/.nanobot/system.db`)
- Web API endpoints under `/api/v1/calendar/events`
- Web UI pages: `CalendarPage.tsx`, `CalendarView.tsx`, etc.

However, there is **no agent-facing calendar tool** today. The agent cannot create calendar events via natural language.

## Approaches Considered

### Approach A: Pure Skill (via inline Python script)
Create a single `SKILL.md` under `nanobot/skills/calendar/SKILL.md` that teaches the LLM to:
1. Parse the date and event title from the user message.
2. Use the `exec` tool to run a short Python snippet that imports `nanobot.storage.calendar_repository.get_calendar_repository()` and calls `create_event()`.

**Pros:**
- Zero changes to Python source code.
- Works even if the web server is not running.
- Direct repository access = no HTTP overhead.

**Cons:**
- Relies on the `exec` tool being available.
- LLM must construct a correct Python script.

### Approach B: Skill + Native Agent Tool
Create a new `calendar` tool in `nanobot/agent/tools/calendar.py`, register it in the tool registry, and provide a thin skill that tells the LLM when to use it.

**Pros:**
- More structured; explicit tool schema for the LLM.
- Easier to extend later (update, delete, query events).

**Cons:**
- Requires modifying the agent tool registry and loop.
- Overkill for the immediate requirement of "record an all-day event with no reminder."

### Approach C: Pure Skill (via HTTP API)
Teach the LLM to call `curl http://127.0.0.1:6788/api/v1/calendar/events`.

**Pros:**
- Reuses existing Web API logic (including reminder-job creation, though we disable reminders).

**Cons:**
- Fragile: requires the web server to be running.
- Needs JSON construction in shell, which is error-prone.

## Decision
**Approach A (Pure Skill via inline Python script)** is selected because it is the lightest-weight, most reliable solution that satisfies the user's explicit request to "add a skill capability." It requires no code changes outside of adding the `SKILL.md` file.

## Design Details

### Skill File
**Path:** `nanobot/skills/calendar/SKILL.md`

**Frontmatter:**
```yaml
name: calendar
description: Record calendar events from natural language. Creates all-day events with no reminders.
short_description: "Record calendar events"
keywords: "calendar, schedule, event, 日历, 日程, 安排, 记录, 明天, 后天, 下周, 会议, 约会, 提醒"
category: "utility"
```

**Body Content:**
- Explain the existing calendar backend (`~/.nanobot/system.db`).
- Provide a reusable Python snippet template for `exec`:
  ```python
  import sys
  from pathlib import Path

  # Ensure nanobot is importable
  project_root = Path(__file__).resolve().parent if '__file__' in dir() else Path.cwd()
  while project_root.name != 'nanobot-webui' and project_root.parent != project_root:
      project_root = project_root.parent
  if str(project_root) not in sys.path:
      sys.path.insert(0, str(project_root))

  from nanobot.storage.calendar_repository import get_calendar_repository

  repo = get_calendar_repository()

  # Optional deduplication: check for existing events on the same day
  title = "<event title>"
  start_time = "<YYYY-MM-DD>T00:00:00"
  end_time = "<YYYY-MM-DD>T23:59:59"
  existing = repo.get_events(start_time=start_time[:10], end_time=end_time[:10])
  if any(e.get("title") == title for e in existing):
      print(f"Event '{title}' already exists on {start_time[:10]}")
  else:
      event = repo.create_event({
          "title": title,
          "description": "",
          "start_time": start_time,
          "end_time": end_time,
          "is_all_day": True,
          "priority": "medium",
          "reminders": [],
      })
      print(f"Created event: {event['id']} — {event['title']} on {event['start_time'][:10]}")
  ```
- Instruct the LLM to parse dates intelligently (today, tomorrow, next week, specific dates).
- Instruct the LLM to confirm the created event to the user.

### Date Parsing Logic
The LLM will handle relative dates in the prompt (e.g. "明天" -> tomorrow's date). For ambiguous expressions, the LLM should ask the user for clarification.

### Constraints Enforced by Skill
- `is_all_day` is always `True`.
- `reminders` is always an empty list `[]`.
- `priority` defaults to `"medium"`.
- The template includes a `sys.path` guard so the import works regardless of the interpreter's working directory.
- The template checks for duplicate titles on the same day before creating a new event.

### Error Handling
- If the `exec` call fails, the LLM should surface the error to the user.
- If the date cannot be determined, the LLM should ask the user before creating the event.

### Testing
- Verify that the skill is loaded by `SkillsLoader` (check `nanobot/skills/calendar/SKILL.md` exists).
- Send a test message like "记录明天要开会" and confirm an all-day event is created in `system.db` with `is_all_day = 1` and empty reminders.

## Future Extensions (out of scope)
- Support non-all-day events with specific start/end times.
- Support adding reminders.
- Support updating or deleting events.
- Support recurring events.
