---
name: todo-planning
description: Executes task lists from .claude/todo-planning/todo.md. Use this skill whenever the user says "run my todo list", "work through my todos", "execute the todo plan", "do the todo", "start the todo", "run todo-planning", or any similar phrase indicating they want Claude to read and perform the tasks in their todo planning file. Always use this skill when the user references .todo_planning or todo.md and wants tasks carried out.
---

# Todo Planning

This skill reads `.claude/todo-planning/todo.md`, performs each task using available tools, tracks progress inline, and archives the completed list.

## Step 1: Find the Todo File

Read `.claude/todo-planning/todo.md`. If it doesn't exist, tell the user and stop — do not proceed.

## Step 2: Parse the List

Determine the list type by looking at how items are written:

- **Unordered list** — items start with `-`, `*`, or `+`. Complete these in whatever order makes the most sense given the tasks (e.g., dependencies, logical grouping). Order is flexible.
- **Ordered list** — items start with numbers (`1.`, `2.`, etc.). Complete these strictly in numbered sequence.

Apply the same rule recursively to nested sublists: unordered sub-items can be done in any order, ordered sub-items must follow their sequence.

Skip any items already marked complete (`[x]` or `[X]`).

## Step 3: Execute Each Task

For every task:

1. **Announce** what you're starting: e.g., `Starting: Add error handling to the payment service`
2. **Do the work** — interpret the natural language description and carry it out using your available tools (read files, edit code, run commands, etc.)
3. **Mark it complete** — update `todo.md` by changing the item to use `[x]`:
   - `- task text` → `- [x] task text`
   - `- [ ] task text` → `- [x] task text`
   - `1. task text` → `1. [x] task text`
4. **Continue** to the next task

### If a Task Fails

Stop immediately. Report:
- Which task failed
- What went wrong and why

Then ask: **"Should I continue to the next task, or abort the todo list?"** Wait for the user's answer before doing anything else. If they say abort, stop. If they say continue, mark the failed task with a note (e.g., `- [!] task text — failed: reason`) and move on.

## Step 4: Archive the Completed List

Once all tasks are checked off:

1. Scan `.claude/todo-planning/` for files matching `N_todo.md` (where N is a number)
2. Find the **highest** existing number — if `1_todo.md` and `3_todo.md` both exist, the highest is `3`
3. Rename `todo.md` to `{highest + 1}_todo.md` (e.g., `4_todo.md`)
4. Confirm to the user: `All tasks complete. todo.md archived as 4_todo.md.`

If no numbered files exist yet, rename to `1_todo.md`.
