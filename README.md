# Cobbler

Like the elves in the shoemaker's tale, a small team of Claude agents works through the night on the projects you give them, and leaves the results for you in the morning.

[![Cobbler explainer: 71 seconds, no sound](docs/cobbler-explainer.gif)](docs/cobbler-explainer.mp4)

*71-second explainer, no sound. [Download the MP4](docs/cobbler-explainer.mp4). The video is code too: see [`video/`](video/).*

Each project gets its own topic folder: a charter you write, plus the agent's own state, log and pitch. Every night an orchestrator reads every topic's pitch and shares out a fixed weekly budget. The chosen agents work in sandboxes that can see only their own folder. By morning you get one short digest on your phone. You steer by editing Markdown files. It runs headless with Claude Code on a Linux machine that stays on overnight.

## Set up on your own machine

**What you need**
- **A Claude Pro or Max subscription.** The agents spend your plan's usage, not an API bill.
- **Claude Code, the command-line tool.** Claude Desktop alone can't do this: the overnight runs are Claude Code running headless, with nobody at the keyboard. Claude Code comes with the same subscription. Install it with `curl -fsSL https://claude.ai/install.sh | bash`, then run `claude` once to sign in. You can keep using Claude Desktop (or Obsidian, or any text editor) to read the digests and edit charters, because everything is plain Markdown files.
- **A Linux machine that stays on overnight**, such as a mini PC, an old laptop or a home server, with `bubblewrap` (`sudo apt install bubblewrap`) and Python 3.11+.
  - **Mac:** not supported yet. The sandbox (bubblewrap) and scheduler (systemd) are Linux-only. A port would use launchd and macOS's own sandbox.
  - **Windows:** try WSL2 (Ubuntu) with systemd enabled, on a PC set never to sleep. Untested.

**Steps**
1. Clone this repo, and name the remote `template`, so it's clear it's where updates come from and never where your work goes:
   `git clone -o template https://github.com/mcraeian01/cobbler.git`
   The folder can live inside an Obsidian vault, which makes it pleasant to steer from your phone.
2. Run `./setup.sh --check`, install anything it says is missing, then run `./setup.sh`. It copies `config.example.toml` to `config.toml`, asks your first name (the agents use it), and asks for a token: open another terminal, run `claude setup-token` and paste what it prints. It also picks a private ntfy topic for the digest; subscribe to it in the ntfy phone app. The token and topic go in `~/.config/agent-runner/env`, outside this folder.
3. In `config.toml`, set `weekly_reset_weekday` and `weekly_reset_time` from Claude → Settings → Usage, and set `[schedule]` to the times you want. The weekly cycle should be the evening before the reset. Re-run `./setup.sh` after changing the schedule.
4. Seed a topic: `./new-topic.sh investor "Investor"`, then write its `CHARTER.md` (`examples/investor/CHARTER.md` is a worked example) and give it a priority in `_orchestrator/POLICY.md`.
5. Try one run by hand: `set -a; . ~/.config/agent-runner/env; set +a; bin/cycle.py --only investor --tier S`. From then on the timers take over.

## Your copy stays private
This repo is the **template**: the engine, prompts and rules. Your clone is an **instance**: the same engine plus your topics, ledger, digests, priorities and config, committed locally.
- **Get updates:** `git pull template main`. Engine changes merge in; your topics and history are untouched. A conflict can only come from a file you've both edited, such as `POLICY.md`.
- **Never push to `template`.** You don't have write access anyway. To back up your instance, add a remote of your own that is **private**, e.g. `git remote add backup git@github.com:<you>/cobbler-mine.git`, and push there.
- **Secrets never touch git:** the token, ntfy topic and any broker keys live in `~/.config/agent-runner/env`.
- **Improving the engine:** make the change in a separate clone of the template and open a pull request, so none of your instance's history comes along.
- **Or keep your own public copy:** work in your instance and release engine changes to a public repo now and then with `bin/release.py`. It copies only an allow-list of engine files, blanks the seed files, and refuses if anything matches your private register (`private.toml`: names, accounts, machines, private projects) or a real secret, a home path or the hostname. It commits with `--commit` and pushes only with `--push`.

## How a cycle works
1. A systemd timer starts `bin/cycle.py`.
2. The runner gives the **orchestrator** read access to everything, plus facts it computes itself: credits left this week, last wake per topic, last outcomes. The orchestrator settles last cycle, scores pitches against `POLICY.md`, and returns a plan: which topics to wake, at which tier, each with a short **wake message**, plus a digest.
3. The runner checks the plan against `config.toml` (known topics, tiers, max agents, credits left) and drops anything that breaks the limits.
4. Each woken **topic agent** runs in its own sandbox. It sees its topic folder (read-write) and `_global/` (read-only), nothing else. A hard turn ceiling stops runaway runs.
5. The runner records what each run actually used, updates `_orchestrator/USAGE.md`, and pushes the digest to your phone.

## Why Claude Code
You could get part of the way with scheduled tasks in Claude Desktop: one task per project, plus an orchestrator task that writes a plan the others read. Cobbler uses Claude Code in headless mode instead, because the parts that make it trustworthy overnight have to be **code, not prompts**:

- **Limits are enforced, not requested.** The runner, plain Python, starts every run. It sets a hard turn ceiling, rejects plans that name unknown topics or blow the budget, and drops any grant over the cycle's spend cap. A prompt can only ask a model to stay within budget at 4:40am.
- **Usage is measured, not self-reported.** Each headless run returns its actual turn count and an estimated cost, and the ledger is built from those numbers. In testing, an agent logged "about 9 turns" for a run the runner measured at 16; a ledger built on self-reports would drift.
- **Isolation is a wall, not an instruction.** Each agent runs in a bubblewrap sandbox that can see only its own topic folder and the shared rules. It cannot see other topics, the rest of your files or your credentials. That matters, because agents read web pages that may try to steer them.
- **Keys stay out of the agent's reach.** Some steps shouldn't involve a model at all. The paper-trading broker is a plain script outside the sandbox: it holds the API keys, checks each order against the charter's limits, and only then places it. The agent only writes an orders file. Without code between the agents, the agent would have to hold the keys itself.
- **Every run starts fresh, with only what it needs.** The orchestrator and each agent are separate sessions that hand over through files. Nothing piles up in one long conversation, so there's no mixing of projects and no lossy compacting. Each role can use a different model: a cheaper one for allocation, a stronger one for the work.
- **Structured hand-offs.** The orchestrator must return its plan in a fixed JSON schema, which the runner checks before anything wakes.
- **APIs over clicking.** Anything with an API (a broker, a data source) is called directly, and the result can be checked. Cobbler deliberately avoids computer use. Clicking around a screen unattended is fragile, and anything planted on a page could steer an agent that can reach your whole desktop.
- **Your subscription, not an API bill.** Headless Claude Code runs on a Pro or Max plan's usage, using a long-lived token from `claude setup-token`. The weekly cycle exists to spend allowance that would otherwise expire at your reset.

**Where Claude Desktop is still better:** no setup, built-in connectors (mail, calendar, drive) and computer use. The two work side by side: a Desktop task can drop a file into a topic's folder, and that topic's agent picks it up on its next wake. Keep the rule-bound overnight work in the runner.

## Books: one simulated portfolio per thesis
A topic listed under `[books.<slug>]` in `config.toml` runs many simulated **books**, one per thesis, each starting at the same size (default $1M). The agent writes a short spec per book in `topics/<slug>/books/<ID>.json`; `bin/books.py`, plain code outside the sandbox, runs them:

```json
{"id": "B001", "thesis": "T1", "status": "active",
 "targets": [{"symbol": "IEI", "kind": "etf", "weight": 0.5},
             {"symbol": "TLT", "kind": "etf", "weight": 0.3},
             {"symbol": "TLT271217C00095000", "kind": "option", "weight": 0.15}],
 "kill": [{"if": "nav_below", "value": 0.75},
          {"if": "close_below", "symbol": "TLT", "value": 82},
          {"if": "after", "value": "2027-11-05"}]}
```

- **Fills:** a new or changed spec fills at the **open of the first trading session after it was written**, at real prices from Alpaca's market data, plus slippage. Nothing is back-dated and no orders are placed, so the number of books isn't limited by an account's size. Each leg fills on its own, at the open of the first such session in which it traded: a thinly traded option doesn't hold the rest of the book back, and a buy leg with no trade in 5 sessions is dropped (its weight stays in cash). Option contracts are checked when a spec arrives: one that doesn't exist is rejected, and a thin one is accepted with a warning.
- **Kill rules** are checked on every session's close: value below a fraction of the start, a symbol closing above or below a level, or an end date. A hit sells the whole book at the next open, mechanically, even on nights the agent isn't woken.
- **Limits** from `config.toml`: open books, and the pace of new books (a weekly limit, with an optional daily ramp until a set number have opened); one company at most 20% of a book (ETFs and baskets can go to 100%); options bought only, at most 20% each and 50% in total; longs at most 100% (no margin); shorts at most 50%.
- **Held at Alpaca (`mode = "alpaca"`):** each book gets its own Alpaca paper account from a private accounts file, never reused. The runner turns the spec into real orders on that account (market orders for stocks and ETFs, limit orders at the quote for options) before the open, reads positions and value back from Alpaca every cycle, re-sends legs that didn't fill, and sells out when a kill rule fires. The orders, fills and values are then Alpaca's records, not ours. Alpaca allows 3 paper accounts per login, so plan on more logins as books grow. `bin/accounts.py plan N` reserves the next N accounts across logins and prints a checklist for Alpaca's dashboard (sign-ups, accounts and keys can't be created by API); `bin/accounts.py check` tests each account's keys, number, balance and options level, and only accounts that pass are used. A private register (`_private/ACCOUNTS.md`, never released) shows every account, its login, and the book and thesis it holds. Without it (`mode = "sim"`) books are simulated here at real prices.
- **`BOOKS.md`** in the topic, refreshed every cycle: every book's value, return, SPY over the same period, positions, kill rules, events, and the verdict on each spec.
- Options settle at intrinsic value after expiry. Dividends, splits and borrow fees aren't modelled.
- Change a book by editing its spec; set `"status": "close"` to sell it out. A closed book stays closed; a new idea gets a new ID.
- Tests: `python3 tests/test_books.py`.

## Timestamped record
A thesis is only convincing if you can prove it was written before the money went in. Cobbler can timestamp a topic's chosen files with [OpenTimestamps](https://opentimestamps.org), which anchors a fingerprint in the Bitcoin blockchain, in one of two ways. Both run after the night's agents, so a book accepted tonight is stamped hours before it fills.

**Sealed, private (recommended): `[seal.<slug>]`, `bin/seal.py`.**
- Every cycle the runner fingerprints each item (each `theses/<ID>.md`, or each thesis section of a single `THESES.md`, and each `books/*.json`), keeps every version in `_records/<slug>/`, and stamps the full list. Nothing is published.
- Later, `bin/seal.py <slug> --reveal B007` builds a folder to share: that book's spec and its thesis exactly as first sealed, the stamped lists proving they existed before the book filled, and how to check them. The lists also carry every other item's fingerprint, so the size of the field is provable without revealing any other thesis.
- Items are never dropped from the list, so the field can't be shrunk after the fact.
- One file per thesis is simplest to share later. A single `THESES.md` also works: it's split on headings that start with an ID (`### T3 · …`).

**Public: `[publish.<slug>]`, `bin/publish.py`.** Pushes the chosen files to a public GitHub repo after every cycle, append-only, with stamped manifests. Nothing goes out if a file contains a secret from the env file, a token or key, a home path or the hostname. The push uses a deploy key for that one repo. One-time setup, by you: `bin/publish-setup.sh <owner>/<repo>`.

Tests: `python3 tests/test_seal.py`, `python3 tests/test_publish.py, tests/test_seal.py`.

## Topics that trade
A topic listed under `[brokers.<slug>]` in `config.toml` gets an Alpaca **paper** account, run by `bin/broker.py` outside the sandbox. Before the agent runs, the runner writes `BROKER.md` into the topic: account value, positions, fills, value against SPY. After the run, it reads the agent's `orders.json`, checks each order against the limits in `config.toml` (probe and position size, a thesis ID and reason on every order), places the ones that pass, and reports every verdict in `BROKER.md` and the digest.

The keys (`ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` in the env file) never enter a run: the runner passes the sandbox only the Claude token and basic shell variables. Only Alpaca's paper endpoint is ever called. Set `enabled = false` to hold orders without stopping the topic.

By hand: `bin/broker.py <slug> --snapshot`, `--check` (validate `orders.json`, place nothing), `--execute`.

## Topics that review security
A topic listed under `[scanners.<slug>]` in `config.toml` reviews your repos and this machine. A review agent has to read code, which is what the sandbox exists to withhold, so it makes a trade: it can see the code but has no web tools, whatever `[agents]` says. Anything it reads, including instructions planted in it, has no way out.

Every cycle, before the orchestrator runs, `bin/scanner.py` works outside the sandbox with no model involved. It copies each repo's tracked files to a read-only snapshot, withholding key files and redacting lines that look like secrets. It checks locked dependencies against [OSV](https://osv.dev) and checks the host (open ports, pending updates, firewall service, sshd settings, file modes). The results go in `SCAN.md`. The snapshot lives in the runner's state folder, so copies of your code never reach a synced vault. `SCAN.md` does: it says where weaknesses are, so treat the vault accordingly.

The agent writes patches to `outputs/patches/<repo>--<what>.diff`. The runner dry-runs each one against the snapshot and reports the verdict in the digest. Nothing is ever applied: read the patch, then `git apply` it on a branch yourself.

The topic argues for its own cadence with `Not before` in its pitch, using the history in `SCAN.md` as evidence. A new serious finding puts a `SCAN ALERT` in the orchestrator's facts, which overrides that date. `examples/security/CHARTER.md` is a worked example.

**Wider scope, same reach.** The topic can cover more than this machine, but only through evidence brought to it. Its tools and mounts never grow, and the tools are a hard allow-list, so adding one to `[agents]` for other topics doesn't reach it. A scanning topic won't run outside the sandbox, and `SCAN.md` states its reach every cycle.
- **Other computers push.** `bin/posture.sh` reads a machine's settings and writes a small report to `_inbox/machines/<name>.md`, carried by whatever syncs the folder. List the name under `machines`. The runner holds no key to that machine, so compromising it gains nothing on the others. Install a copy outside the synced folder: scheduling the synced file itself would let anything that writes to the folder run code there. Reports are read as data (known fields, plain values), and a missing or stale one shows as a coverage problem.
- **Accounts are read by plain code.** With `github_user` set, the runner finds your public repos and scans them with no credentials. An optional read-only token adds private repos and GitHub's alerts. A classic token that can write is refused; a fine-grained token doesn't declare its permissions, so keep it read-only when you create it.
- **The rest is a register.** Phones and accounts with no read-only route stay in the topic's `ASSETS.md`, marked as unobserved. The agent asks you about them and never logs in.

This is a first line, not an audit. It can't see uncommitted work, git history, unlocked dependencies or firewall rules, and `SCAN.md` lists what it skipped. Redaction matches known patterns, so a secret in an unusual format can still reach the agent, though it can't leave.

By hand: `bin/scanner.py <slug> --scan`, `--patches` (print verdicts).

## Layout

```
bin/cycle.py               the runner
bin/broker.py              paper-trading step for topics under [brokers] (keys stay outside the sandbox)
bin/books.py               simulated books for topics under [books], priced from real market data
bin/live.py                live books: one Alpaca paper account per book (mode = "alpaca")
bin/accounts.py            plan and check those accounts; keeps the private register of what each holds
bin/scanner.py             scan step for topics under [scanners] (redacted code snapshot, OSV, host and account checks)
bin/posture.sh             read-only posture report, run on your other computers
bin/seal.py                private, timestamped record for topics under [seal], with selective reveal
bin/release.py             release engine changes from your instance to a public copy, checked against private.toml
bin/publish.py             public, timestamped record for topics under [publish]; bin/publish-setup.sh sets it up
tests/                     tests/test_books.py, tests/test_broker.py, tests/test_publish.py, tests/test_seal.py, tests/test_scanner.py, tests/test_scanner_scope.py, tests/test_release.py, tests/test_live.py, tests/test_accounts.py
config.toml                hard limits: pool, tiers, models, schedule (from config.example.toml)
deploy/                    systemd unit templates and env template, installed by setup.sh
setup.sh                   checks prerequisites, writes the private env file, installs the timers
examples/                  worked example charters
docs/                      explainer video (MP4) and its GIF preview
video/                     the explainer's source (Remotion): edit video/src/scenes.ts, render with npm
_global/
  AGENT_PROMPT.md          prompt every topic run starts with
  BOUNDARIES.md            rules every topic inherits
  RUN_PROTOCOL.md          steps every topic run follows
_orchestrator/
  ORCHESTRATOR_PROMPT.md   prompt the orchestrator starts with
  ORCHESTRATOR_PROTOCOL.md
  POLICY.md                you write: priorities, weights, fairness, proposal limits
  FEEDBACK.md              you write: relayed to a topic on its next wake
  WAKE_MESSAGE.md          format and rules for the wake message
  LEDGER.md                scores, grants, delivery record
  USAGE.md                 written by the runner: actual turns and cost per run
  PROPOSALS.md             proposed new topics, awaiting your approval
  digests/                 one digest per cycle
_inbox/machines/           posture reports pushed by other computers
_template/                 copied to seed a new topic
  CHARTER.md  STATE.md  LOG.md  PITCH.md  outputs/
topics/<slug>/             one isolated folder per topic
new-topic.sh               seeds a topic
```

## Who writes what

| File | You | Orchestrator | Topic agent |
|---|---|---|---|
| `config.toml`, `_global/*`, `POLICY.md`, `FEEDBACK.md` | edits | reads | `_global` only |
| `LEDGER.md`, `PROPOSALS.md`, `digests/` | reads, approves | writes | no access |
| `USAGE.md` | reads | reads | no access |
| `CHARTER.md` | writes | reads | reads |
| `STATE.md` → Owner's notes | writes | reads | reads, marks done |
| `STATE.md` → rest, `LOG.md`, `PITCH.md`, `outputs/` | reads | reads | writes |
| `BROKER.md` (trading topics) | reads | reads | reads; written by the runner |
| `orders.json` (trading topics) | reads | reads | writes; placed and cleared by the runner |
| `books/<ID>.json` (book topics) | reads | reads | writes |
| `BOOKS.md` (book topics) | reads | reads | reads; written by the runner |
| `SCAN.md` (security topics) | reads | reads | reads; written by the runner |

## Common actions
- **Add a topic:** `./new-topic.sh <slug> "<Title>"`, fill in its CHARTER.md, set its priority in POLICY.md. The next cycle picks it up.
- **Steer a topic:** add a line to `FEEDBACK.md` (relayed on its next wake), or write in "Owner's notes" in its STATE.md.
- **Run one topic now:** `bin/cycle.py --only <slug> --tier S`
- **Pause a topic:** set its priority to 0 in POLICY.md.
- **Approve a proposal:** mark it `approved` in PROPOSALS.md and seed it.
- **Change the budget:** edit `config.toml`.

## Licence
MIT. See `LICENSE`. The explainer's source uses [Remotion](https://www.remotion.dev), which has its own licence: free for individuals and small teams.
