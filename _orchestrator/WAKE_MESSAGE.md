# Wake message

The orchestrator puts this in each wake's `message`; the runner appends it to the agent's prompt. It is the only thing a topic agent receives from outside its own folder, so it must be short, factual and light on direction.

## Format

```
WAKE · <slug> · <YYYY-MM-DD HH:MM>
Allocation: <tier> · <credits> credits · <turns> turns
Woken because: selected (score <n>, rank <r> of <total>) | starved (<d> days unwoken)
Last wake: <n days/hours ago> | first wake
From owner: <condensed feedback> | none
Context: <critical facts> | none
```

## Rules
- Keep it under 80 words. Every line is either a fact or the owner's words.
- Never tell the agent what to do next. The agent chooses its own step from its own state and pitch.
- If the allocation is smaller than the pitch asked for, say so as a fact ("asked M, granted S"). Leave the agent to rescope.
- **From owner:** relay feedback from `FEEDBACK.md` faithfully. Condense the wording, keep the meaning, add no interpretation of your own. Quote short phrases where tone matters.
- **Context:** include a fact only if a sensible agent would act differently knowing it. Examples: The owner is away until Friday; a deadline moved; the weekly pool is nearly spent. Leave out scores breakdowns, other topics' work, and your own opinions on the agent's plan.
- Never pass another topic's content, unless the owner's feedback explicitly asks for it to be shared.
