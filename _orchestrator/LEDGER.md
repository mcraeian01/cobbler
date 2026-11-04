# Ledger

Append only. The orchestrator writes one entry per cycle. Week runs from the weekly reset set in POLICY.md.

```
## YYYY-MM-DD HH:MM · cycle
Credits at start: n/pool
Settled: <topic> delivered|partial|missed|overran (granted X, used ~Y)
Scores: <topic> 72 (pri 28, notes 20, record 12, momentum 8, pitch 4)
Granted: <topic> M (2), <topic> S (1)
Spent this cycle: n (incl. 1 orchestrator)
```
