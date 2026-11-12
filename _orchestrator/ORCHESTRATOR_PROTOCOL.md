# Orchestrator protocol

The orchestrator is the only run that sees every topic. It allocates scarce credits, wakes agents, and reports to the owner. It does no topic work itself and writes nothing inside topic folders.

## Access
- Reads: `_global/`, `_orchestrator/`, and every `topics/<slug>/` folder.
- Writes: `_orchestrator/` files only. Topic folders are read-only.
- Returns a plan. The runner validates it, wakes the agents and sends the digest.

## 1. Gather
1. Read `POLICY.md`, `FEEDBACK.md`, `USAGE.md`, `LEDGER.md` (last 2 weeks), `PROPOSALS.md`, and the runner facts at the end of your prompt.
2. For each topic: read `PITCH.md`, the "Owner's notes" and "Needs owner" sections of `STATE.md`, and the latest `LOG.md` entry.
3. Credits left this week come from the runner facts. Do not recompute them.

## 2. Settle the last cycle
For each topic woken last cycle, compare the ledger's grant with its latest LOG entry, PITCH and the runner's outcome in `USAGE.md`:
- Did it run? Did it deliver what its pitch promised? Did it stay within its turns, or hit the runner's ceiling?
- Record the result in `LEDGER.md` as delivered, partial, missed or overran.

## 3. Score and allocate
1. Skip topics with priority 0, pitches marked `idle` or `done`, and topics woken last cycle that have not yet logged a run. Also skip a pitch whose `Not before` date is still ahead, even when credits would otherwise expire, unless the runner facts show a `SCAN ALERT` for that topic or the owner has notes or feedback waiting for it.
2. Score each remaining pitch with the weights in `POLICY.md`. Show the arithmetic in the ledger.
3. Apply fairness rules first, then grant in score order until the runner's agent spend cap or the max-agents limit is hit. In a `weekly` cycle the cap is everything that would otherwise expire at the reset, so spend it.
4. A grant can be smaller than the ask.
5. The spend cap is the reserve: the runner divides what is left by the cycles left this week, after setting aside the orchestrator's own cost for each. Grants beyond it are dropped, so plan inside it.

## 4. Wake
For each funded topic, compose a wake message following `WAKE_MESSAGE.md` exactly and add it to the plan's `wakes` (topic, tier, reason, message). Then mark any feedback you relayed as `relayed YYYY-MM-DD` in `FEEDBACK.md`.

The agent wakes into its own state plus this message, and nothing else. Keep it that way: no plans, no suggestions, no other topics' content.

## 5. Look for new initiatives
Only if `POLICY.md` allows a proposal this cycle. Look across all states for a gap: a recurring "Needs owner" theme, two topics blocked on the same missing thing, or a stated goal no topic owns. Write at most one proposal to `PROPOSALS.md` with: name, goal, why now, evidence (which topics), what it would displace, suggested tier and priority.

## 6. Digest for the owner
Fill the plan's digest fields. After the agents finish, the runner assembles them with a Progress section built from each agent's fresh pitch, saves the result to `_orchestrator/digests/` and sends it to the owner's phone. Don't write a digest file yourself. Plain text only: phones show Markdown as typed. Write topic names in capitals (INVESTOR, SECURITY). Each item is one short line.

- `proposals`: `<name>: <one line>. Reply to approve.` Empty if there are none.
- `selection`: one per wake: `<TOPIC> · <tier> · <credits> credits: <why, in under 15 words>`. Say so if the grant is smaller than the ask.
- `not_picked`: `<TOPIC>: <why, in under 15 words>`, for each active topic not woken.
- `needs_you`: `<TOPIC>: <decision>, blocking <what>`. Any `URGENT` line from a topic's "Needs owner" goes first, starting with URGENT, and the runner then leads the digest with this section.

Feedback on unfunded pitches goes in the digest only. Agents never see it unless the owner adds it to `FEEDBACK.md`.

## 7. Ledger
Append the cycle to `LEDGER.md`: date, credits left at start, each grant with its score and wake reason, and the settle results from step 2.
