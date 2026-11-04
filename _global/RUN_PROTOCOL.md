# Run protocol

Every run of every topic follows these steps. Your context is your own topic folder, the global folder named in your prompt, and the wake message at the end of your prompt. Nothing else.

## 0. Read the wake message
It gives your allocation, why you were woken, time since your last wake, any feedback from the owner, and any critical context.
- The turn allocation is your budget. Each tool call you make is roughly one turn. Keep a rough count.
- The runner stops the run at your allocation plus a small buffer. Past that, nothing you are doing gets saved, so finish well inside it.
- Feedback from the owner outranks your own plans. Context is information to weigh.

## 1. Orient (keep it short)
1. Read `BOUNDARIES.md` in the global folder.
2. Read your `CHARTER.md`, `STATE.md`, `PITCH.md`, and the last 3 entries of `LOG.md`.
3. Check `STATE.md` → "Owner's notes".

## 2. Choose
The step is yours to choose. Usually it is the next step from your own pitch. Change it when the owner's feedback or notes call for something else, or when the allocation is smaller than you asked for and the step needs rescoping.

Write one line in the log saying what you picked and why before you start.

## 3. Work
- Stay inside your topic folder and whatever the charter's "Allowed sources and tools" lists.
- Put anything the owner should read or use in `outputs/`, named `YYYY-MM-DD-short-name.md` (or the right extension).
- Reserve about 5 turns for steps 4 and 5. At your allocation minus 5, stop and write back, even mid-task. Note where you stopped.

## 4. Write back
1. Update `STATE.md`: status, backlog, "Needs owner", key files.
2. Append a LOG entry in the format at the top of `LOG.md`. Copy the wake message's allocation and reason into it, with your rough turn count.
3. Leave Owner's notes alone, except to mark an item `(done YYYY-MM-DD)`.

## 5. Pitch
Rewrite `PITCH.md`. This is how the topic earns its next run.
- Be honest about what was delivered. Claims are checked against the LOG and outputs, and overclaiming lowers future allocations.
- Ask for the smallest tier that does the next step well.
- Set status to `idle` if the next step needs the owner first, and `done` if the charter's "done means" is met.

## No messages to the owner
Agents cannot message the owner. The orchestrator collates everything into one digest. If something is time-critical, put it first under "Needs owner" and start the line with URGENT; the orchestrator leads the digest with it.
