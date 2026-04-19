---
name: kaizenos-cli
description: >
  Manage Sakib's life and work through the Kaizen OS CLI (`kaizenos`). This skill is the ONLY way
  AI agents interact with Sakib's personal operations system — use it whenever the user wants to
  manage tasks, track habits, run focus sessions, plan goals, review their day/week, check their
  calendar, or do anything with areas, epics, quests, subquests, notes, routines, or priority rules.
  Triggers: "add a task", "what's overdue", "morning briefing", "show my dashboard", "mark habit done",
  "start focus", "weekly review", "what should I work on", "plan this goal", "check calendar",
  "triage", "batch complete", "reschedule", "what did I accomplish", "my tasks", "my habits",
  "Kaizen OS", "Merlin", or any personal/professional productivity management request.
  DO NOT trigger for: fixing bugs in source code, writing migrations, reviewing PRs, CI/CD setup,
  refactoring components, or any software engineering task — even if it mentions entities like
  "quests" or "dashboard" in the context of code files, components, or database schemas.
  When in doubt and the user is talking about their life/work rather than code, use this skill.
---

# Kaizen OS CLI — Agent Operations Guide

The `kaizenos` CLI is how you (the AI agent) interact with Sakib's life management system. Sakib uses the GUI dashboard; you use this CLI. Every command talks to the Kaizen OS MCP server over HTTPS.

## Before You Start

Run the health check to confirm the CLI is working:

```bash
kaizenos doctor --json
```

If any check fails, stop and tell the user. Common issues:
- `mcp_key_set` failed: `KAIZEN_OS_MCP_KEY` environment variable is not set
- `server_reachable` failed: network issue or wrong key

The CLI is installed globally via `uv tool install` at `C:\Users\sakib\.local\bin\kaizenos.EXE` in its own isolated Python venv. If `kaizenos` is not found, reinstall with: `uv tool install F:/Tools/Projects/kaizenos-manager/packages/cli`

## How to Invoke Commands

Use this pattern for all commands:

```bash
kaizenos [--json] <command> [subcommand] [args] [options]
```

**Always use `--json`** when you need to parse the output programmatically. JSON responses follow this shape:

```json
{"success": true, "data": ..., "count": N}
{"success": false, "error": "..."}
```

Use the global flag (`kaizenos --json <command>`) or the local flag (`kaizenos <command> --json`) — both work. Note: top-level commands (`today`, `dashboard`, `briefing`, `search`, `accomplishments`) only support the global `--json` flag.

**Windows note**: Windows console (cp1252) can't render Unicode characters that appear in MCP responses *and* in Click help text. Two classes of breakage:
- **MCP response output** — the server returns `→`, emoji, etc. Always prefer `--json` to avoid decoder crashes.
- **`--help` text** — e.g. `kaizenos batch --help` contains a `→` that trips `UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'`. Prefix with `PYTHONIOENCODING=utf-8` to render: `PYTHONIOENCODING=utf-8 kaizenos batch --help`. Same fix applies to any `--help` that hits the same encoding error.

## Entity Hierarchy

```
Areas (life domains: Health, Career, Finance...)
  └── Epics (large initiatives)
       └── Quests (projects with clear goals)
            └── Subquests (tasks — the atomic unit of work)

Notes    — cross-cutting, linkable to any entity
Habits   — daily/recurring checks, grouped into Routines
Focus    — time-blocked work sessions on subquests
```

All entities use UUIDs as identifiers.

---

## Command Reference

### Overview & Search

| Command | What it does |
|---------|-------------|
| `today` | Today's subquests grouped by urgency: overdue, due today, in progress, in focus |
| `dashboard` | Full stats: epic/quest/subquest counts, active quests with progress, areas summary |
| `briefing` | AI-generated daily briefing: top 10 prioritized tasks, habit risks, upcoming deadlines |
| `search <query>` | Full-text search across all entity types |
| `accomplishments` | Recently completed tasks |
| `doctor` | Health check: Python, CLI install, env vars, server connectivity |

### Tasks (Subquests)

The atomic unit of work. "Task" and "subquest" are interchangeable.

```bash
# List tasks with filters
kaizenos tasks list [--status todo|in_progress|done] [--mine] [--quest QUEST_ID] [--focus] [--json]

# Create a task
kaizenos tasks add "Task title" [--quest QUEST_ID] [--assignment merlin|sakib|together] [--effort low|medium|high] [--urgency 0-100] [--duration STR] [--context STR] [--json]

# Quick-add (always returns JSON with the new task ID)
kaizenos tasks quick-add "Task title" [--quest QUEST_ID] [--due DATE] [--tag STR] [--assignment merlin|sakib|together] [--effort low|medium|high] [--urgency 0-100] [--remind DATETIME]

# Read / Update / Complete / Delete
kaizenos tasks get <id> [--json]
kaizenos tasks update <id> [--title STR] [--status STR] [--urgency 0-100] [--assignment STR] [--remind DATETIME] [--json]
kaizenos tasks done <id> [--json]
kaizenos tasks delete <id> [--json]

# Convert task to note
kaizenos tasks to-note <id> [--json]

# Batch operations (always JSON output)
kaizenos tasks batch-complete --ids ID1,ID2,ID3
kaizenos tasks batch-reschedule --ids ID1,ID2,ID3 --date 2026-04-20
kaizenos tasks batch-delete --ids ID1,ID2,ID3
```

### Quests

Projects with a clear goal and measurable progress.

```bash
kaizenos quests list [--json]
kaizenos quests add "Quest title" [--json]
kaizenos quests get <id> [--json]
kaizenos quests status <id> [--json]          # includes subquest statistics
kaizenos quests update <id> [--title STR] [--description STR] [--status STR] [--json]
kaizenos quests delete <id> [--json]
```

### Epics

Large initiatives that contain multiple quests.

```bash
kaizenos epics list [--json]
kaizenos epics add "Epic title" [--json]
kaizenos epics get <id> [--json]
kaizenos epics update <id> [--title STR] [--json]
kaizenos epics delete <id> [--json]
```

### Areas

Life domains (Health, Career, Finance, etc.).

```bash
kaizenos areas list [--json]
kaizenos areas add "Area title" [--json]
kaizenos areas get <id> [--json]
kaizenos areas update <id> [--title STR] [--json]
kaizenos areas delete <id> [--json]
```

### Notes

Free-form notes that can be linked to any entity.

```bash
kaizenos notes list [--type meeting|idea|reference|daily|weekly|custom] [--json]
kaizenos notes add "Note title" [--content STR] [--type TYPE] [--json]
kaizenos notes get <id> [--json]
kaizenos notes update <id> [--title STR] [--content STR] [--type TYPE] [--json]
kaizenos notes delete <id> [--json]

# Link/unlink notes to other entities
kaizenos notes link <note_id> <entity_id> [--json]
kaizenos notes unlink <note_id> <entity_id> [--json]

# Convert note to task
kaizenos notes to-task <id> [--json]
```

### Habits & Routines

Daily trackable behaviors, optionally grouped into routines.

```bash
# Daily tracking
kaizenos habits today [--json]                # today's checklist with streaks
kaizenos habits check <habit_id> [--json]     # mark done for today
kaizenos habits uncheck <habit_id> [--json]   # undo

# CRUD
kaizenos habits list [--json]
kaizenos habits add "Habit title" [--routine ROUTINE_ID] [--json]
kaizenos habits update <habit_id> [--title STR] [--routine ROUTINE_ID] [--description STR] [--active|--inactive] [--json]
kaizenos habits delete <habit_id> [--json]

# Routines (habit groups like "Morning Routine")
kaizenos habits routines list [--json]
kaizenos habits routines add "Routine title" [--json]
kaizenos habits routines get <id> [--json]
kaizenos habits routines update <id> [--title STR] [--json]
kaizenos habits routines delete <id> [--json]
```

### Focus Sessions

Time-blocked deep work on a queue of subquests.

```bash
# Manage the focus stack (what you plan to work on)
kaizenos focus list [--json]
kaizenos focus add <subquest_id> [--json]
kaizenos focus remove <subquest_id> [--json]

# Run a focus session
kaizenos focus start [--task-id ID]... [--duration SECS] [--quest QUEST_ID] [--json]
kaizenos focus status [--json]
kaizenos focus tick [--seconds N] [--json]       # add elapsed time
kaizenos focus skip [--save-progress] [--json]   # skip current, advance queue
kaizenos focus pause [--json]
kaizenos focus resume [--json]
kaizenos focus end [--no-save] [--json]
```

`--task-id` is repeatable: `--task-id UUID1 --task-id UUID2` queues multiple tasks. Default block duration is 900 seconds (15 min).

### Calendar

Google Calendar integration (read-only).

```bash
kaizenos calendar events [--json]                          # today's events
kaizenos calendar free-slots [--date DATE] [--json]        # available time blocks
kaizenos calendar busy-times [--start DATE] [--end DATE] [--json]
```

### Entity Links

Bidirectional connections between any two entities.

```bash
kaizenos links add <entity1_id> <entity2_id> [--json]
kaizenos links remove <entity1_id> <entity2_id> [--json]
kaizenos links get <entity_id> [--json]          # all links for an entity
```

### Reviews

```bash
kaizenos review data [--json]           # weekly review: accomplishments, habit adherence, quest progress
kaizenos review save [--notes STR] [--json]
kaizenos review daily [--json]          # today's performance: completed, overdue, habit rate
```

### Priority Rules

Rules that influence task urgency scoring.

```bash
kaizenos rules list [--json]
kaizenos rules add "rule text" [--json]
kaizenos rules get <id> [--json]
kaizenos rules update <id> [--rule STR] [--enable|--disable] [--json]
kaizenos rules delete <id> [--json]
```

### Planning

Smart goal decomposition with deadline parsing.

```bash
kaizenos plan "Build auth system by Friday" [--area AREA_NAME] [--json]
```

Parses natural language deadlines ("by Friday", "by end of month") and generates phased subquests with decreasing urgency and evenly-spaced due dates.

### Batch Operations

Execute multiple operations from a JSON file.

```bash
kaizenos batch ops.json [--fail-fast]
```

The JSON file should contain an array of `{"action": "create|complete|reschedule|delete", "params": {...}}`.

---

## Common Agent Workflows

### Morning Start
```bash
kaizenos doctor --json           # verify connectivity
kaizenos briefing                 # get prioritized daily briefing
kaizenos habits today --json      # check habit status
kaizenos calendar events --json   # see today's meetings
```

### Task Triage
```bash
kaizenos today --json                              # overdue + due today
kaizenos tasks list --status todo --json           # all pending tasks
kaizenos calendar free-slots --json                # when is there time?
```

### Create Task and Start Working
```bash
kaizenos tasks quick-add "Write API docs" --quest QUEST_UUID --urgency 70 --effort medium
# parse the returned ID, then:
kaizenos focus add <new_task_id>
kaizenos focus start --task-id <new_task_id> --duration 1800
```

### End of Day
```bash
kaizenos review daily --json      # today's stats
kaizenos accomplishments --json   # what got done
kaizenos habits today --json      # habit completion check
```

### Weekly Review
```bash
kaizenos review data --json       # full weekly data
kaizenos review save --notes "Completed the auth epic. Need to focus on fitness habits next week."
```

### Goal Planning
```bash
kaizenos plan "Launch MVP by end of April" --area "Career" --json
# creates phased subquests automatically
```

## Assignment Values

Tasks can be assigned to:
- `sakib` — Sakib does it manually
- `merlin` — AI agent handles it (that's you)
- `together` — collaborative effort

## Error Handling

If a command fails, the JSON output will be `{"success": false, "error": "..."}`. Common errors:
- `HTTP 401`: Bad or expired MCP key
- `HTTP 500`: Server-side error — retry once, then report to user
- `ConnectionError`: Network issue or server down
- `At least one field must be specified`: Update commands need at least one option besides the ID
