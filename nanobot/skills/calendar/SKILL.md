---
name: calendar
description: Record calendar events from natural language. Creates all-day events with no reminders.
short_description: "Record calendar events"
keywords: "calendar, schedule, event, 日历, 日程, 安排, 记录, 明天, 后天, 下周, 会议, 约会, 提醒"
category: "utility"
metadata: {"nanobot":{"emoji":"📅"}}
---

# Calendar

Use this skill when the user wants to record something on a specific day.

## Behavior

- Automatically extract the date and event title from the user's message.
- Create an **all-day event** with **no reminders**.
- Confirm the created event to the user.

## How to Create an Event

Use the `exec` tool to run a short Python script that imports `nanobot.storage.calendar_repository` and creates the event directly in `~/.nanobot/system.db`.

### Template

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

### Rules

- `is_all_day` must always be `True`.
- `reminders` must always be an empty list `[]`.
- `start_time` and `end_time` should use ISO format (`YYYY-MM-DDTHH:MM:SS`).
- For all-day events, use `00:00:00` for start and `23:59:59` for end.
- The Web UI uses the `is_all_day` flag to display the event correctly; `end_time` is only a storage convenience.

## Date Parsing

Convert relative expressions to absolute dates before creating the event:

| User says | Date handling |
|-----------|---------------|
| 今天 | today |
| 明天 | tomorrow |
| 后天 | day after tomorrow |
| 下周X | next week's corresponding weekday |
| 下个月X号 | same day next month |
| X月X号 | that specific date (current year) |
| YYYY-MM-DD | use as-is |

If the date is ambiguous, ask the user for clarification before creating the event.

## Examples

**User:** 记录明天要开会  
**Action:** Create an all-day event titled "开会" on tomorrow's date.

**User:** 后天下午去医院  
**Action:** Create an all-day event titled "去医院" on the day after tomorrow.

**User:** 5月1号出去旅游  
**Action:** Create an all-day event titled "出去旅游" on May 1st of the current year.

## Error Handling

- If the `exec` call fails, show the error to the user and suggest trying again.
- If you cannot determine the exact date, ask the user before proceeding.
