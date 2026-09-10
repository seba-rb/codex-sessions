# cx-sessions

A session manager for [OpenAI Codex CLI](https://github.com/openai/codex): list,
filter, open, and delete sessions — including in bulk.

Codex already knows how to do all of this. Its app-server exposes `thread/list`,
`thread/delete`, `thread/archive` and more. What's missing is a front end: the
session picker has no way to delete, and `codex delete` takes one full UUID at a
time.

```
 CODEX_HOME: ~/.codex                                          16 sessions
 ───────────────────────────────────────────────────────────────────────────
 ~/code/my-project  (here)
 >  01a087d7    0d  Refactor the payment retry logic
    019d412b    2d  Add integration tests for webhooks

 ~/code/another-repo
    019d9cc2  145d  Investigate the memory leak in the worker
```

## Install

Requires Python 3.9+ (standard library only — no dependencies) and `codex` on
your `PATH`.

```bash
git clone https://github.com/seba-rb/codex-sessions.git
ln -s "$PWD/codex-sessions/cx-sessions" ~/.local/bin/cx-sessions
```

## Use

```bash
cx-sessions                     # interactive view
cx-sessions ls                  # plain listing
cx-sessions ls --cwd ~/code/api --days 7
cx-sessions ls -g "webhook"     # search title, preview and cwd
cx-sessions rm 019d412b         # by UUID prefix, asks to confirm
cx-sessions prune --older-than 90 --apply
cx-sessions prune --orphans --apply

cx-sessions rename 019d412b "Fix the webhook retries"
cx-sessions rename --auto            # shows what it would name
cx-sessions rename --auto --apply
```

Codex names new sessions on its own, but older ones show a truncated first
message instead. `rename --auto` fills those in by reading the first things you
actually typed and asking a model for a short title. It needs an
OpenAI-compatible endpoint, configured through the environment — nothing is
hardcoded:

```bash
export CX_SESSIONS_NAMER_URL=https://your-endpoint/v1
export CX_SESSIONS_NAMER_TOKEN=...
export CX_SESSIONS_NAMER_MODEL=...
```

Without those it explains the setup and changes nothing. Names are written
straight to the `threads` table because the app-server has no rename method
(`thread/setName` and `thread/name` don't exist), and the database is backed up
first.

It reads `CODEX_HOME` from the environment, so it follows whichever account you
have active. `--all-profiles` merges sessions from every directory matching
`~/.codex-profiles/*` plus `~/.codex`; set `CODEX_SESSIONS_PROFILE_GLOB` to point
somewhere else.

### Session status

Each session shows a status, and the header sums them up
(`2 trabajando · 11 listas`):

| Status | Meaning |
|---|---|
| `trabajando` | a turn is in flight |
| `colgada?` | a turn started but nothing has been written for 5 minutes — the process probably died |
| `lista` | the last turn finished |
| `cortada` | the turn was aborted |
| `huerfana` | the row is there but its rollout file is gone |

These come from the rollout file, not from Codex's daemon. The daemon is the
only thing that knows live state, but its control socket does not speak the
app-server protocol — it answers empty to `thread/list` and to made-up methods
alike. Rollout files, on the other hand, are durable: `task_started` with no
matching `task_complete` means a turn is running.

### Opening a session doesn't tie it to this window

Inside tmux, `Enter` opens Codex in a **new tmux window**, and the agent keeps
working when you leave it. That matters: if Codex ran in the foreground, the
viewer's process would *be* the session, and quitting with `Ctrl+D` would kill
the work in progress.

To get back to the list without closing Codex: `Ctrl-b 0`, `Ctrl-b p`, or click
the window in tmux's status bar — the viewer renames its own window to
`sesiones`, and the Codex one to `codex:<session title>`, so the bar reads

```
0:sesiones  1:codex:Saludo inicial*
```

Closing Codex normally also brings you back: its window disappears and tmux
returns to the list.

Outside tmux it creates a detached session and attaches to it, so `Ctrl-b d`
returns here. Without tmux installed it falls back to running in the
foreground, and says so — there, quitting Codex does end the session.

### Keys in the interactive view

| Key | Action |
|---|---|
| `↑` `↓` / `k` `j` | move between directories and sessions |
| `PgUp` `PgDn`, `g` `G` | page and jump to ends |
| `space` | select / deselect |
| `Enter` | on a session, resume it; on a directory, start a new session there |
| `p` | preview |
| `d` | arm delete; `d` again confirms |
| `a` | archive / unarchive |
| `/` | incremental filter — `Enter` applies, `Esc` cancels |
| `t` | toggle active / archived |
| `r` | reload |
| `q` | quit |

Sessions are grouped by working directory, newest group first. The directory you
launched from always shows up, even with no sessions yet, so you can start one
there with `Enter`.

Deleting takes two keystrokes rather than a modal: the first `d` arms it and says
so on the row, the second one does it, and any other key cancels.

## Why it talks to the app-server

Session state lives in `state_5.sqlite` under your `CODEX_HOME`, and reading it
directly is tempting. That filename is on its fifth version, which says enough
about how stable that contract is. The app-server protocol is what Codex's own
TUI and desktop app use, so it's the better bet across upgrades — and it reports
the methods it accepts, so when one disappears this tool can say which one
instead of breaking in a confusing way.

The one exception is `prune --orphans`. `thread/delete` requires the rollout file
to exist; a row whose `.jsonl` is gone can't be deleted through the API and stays
stuck in your list forever (see
[openai/codex#36558](https://github.com/openai/codex/issues/36558)). That command
is the only one that writes to the database, and it backs it up first.

## Known Codex quirks this surfaces

- **`already has an active writer`** when deleting: a process still holds that
  session open — usually the long-lived `codex app-server` daemon, which keeps a
  lock per session under `<CODEX_HOME>/thread-writer-locks/` and doesn't release
  it when you `/quit`. Restart that daemon to free it.
- **Skills are invoked with `$name`, not `/name`** in Codex
  ([openai/codex#11817](https://github.com/openai/codex/issues/11817), closed as
  not planned). Unrelated to this tool, but it trips up everyone coming from
  Claude Code.

## License

MIT
