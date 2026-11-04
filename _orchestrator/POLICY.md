# Orchestrator policy

The owner writes this file. The orchestrator reads it every cycle and never edits it. This is where you dial in priorities.

## Budget
Budgets are expressed as credits and turns. The numbers live in `config.toml` beside this folder, where the runner enforces them: weekly pool, max agents per cycle, and the tiers. The runner passes the current numbers into every orchestrator run.

What each tier is for:

| Tier | Good for |
|---|---|
| S | a tidy-up, one short draft, answering Owner's notes |
| M | a solid research pass or one draft section |
| L | a substantial build or a full chapter draft |

Unspent credits do not roll over past the weekly reset. `USAGE.md` shows what each run actually used.

## Priority weights
How the orchestrator scores each pitch. Weights sum to 100.

| Factor | Weight | Meaning |
|---|---|---|
| Owner's priority | 35 | the topic priority below |
| Owner's notes waiting | 20 | unanswered instructions from the owner in STATE.md |
| Track record | 20 | did past grants produce the promised outputs (from LEDGER.md) |
| Momentum | 15 | work half done that is cheaper to finish now |
| Pitch value | 10 | the case the agent makes for its next step |

Pitch value is weighted lowest on purpose. Evidence beats salesmanship.

## Topic priorities
1 to 5, where 5 is highest. Topics not listed default to 2. Set a topic to 0 to pause it without deleting its schedule.

| Topic | Priority |
|---|---|
| | |

## Fairness
- Any active topic with priority 2 or more that has gone **5 days** unwoken gets an S grant, if its pitch asks for one.
- No topic gets more than **40%** of the weekly pool.

## New initiatives
The orchestrator sees every topic's state, so it may spot gaps worth a new topic. Limits that keep this from drowning out active work:
- At most **1** proposal per cycle, and at most **2** open proposals at a time.
- Proposals go to `PROPOSALS.md` and the digest. The orchestrator never seeds a topic itself.
- Exploration spending (scoping a proposal) is capped at **10%** of the weekly pool, taken only after active topics' grants.
- A proposal must name the topics it would draw time away from.
