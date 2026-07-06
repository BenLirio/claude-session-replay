# claude-session-replay

A [Claude Code skill](https://code.claude.com/docs/en/skills) that turns any past
Claude Code session into a **self-contained HTML file that replays the session inside
a fake terminal window** — user prompts get typed in live, Claude "thinks" with a
spinner, tool calls run and print their output, exactly like watching the real thing.

**Why:** live Claude Code demos are slow and unpredictable — Claude can take minutes
on a step and the audience loses the thread. Record a good session once, then replay
it on demand: paced for humans, pausable, seekable, and 1×–8× speed.

## Install

As a personal skill (available in every project):

```bash
git clone https://github.com/BenLirio/claude-session-replay ~/.claude/skills/session-replay
```

Or as a project skill: clone (or submodule) it into `.claude/skills/session-replay`
inside your repo.

## Use

In Claude Code, just ask:

> "Make a replay of the session where we set up fastlane"
>
> "Turn session 867bc33a into a demo I can show at standup"

Or run the script directly — no Claude required, no dependencies beyond Python 3:

```bash
# browse recent sessions (id, project, title)
python3 scripts/replay.py --list

# generate the replay (session id prefixes work)
python3 scripts/replay.py 867bc33a -o fastlane-demo.html

# a transcript file from another machine works too
python3 scripts/replay.py path/to/session.jsonl -o demo.html
```

Open the HTML in any browser. It's a single file with zero network requests — email
it, commit it, or host it as static content.

## Player controls

| Control | Action |
|---|---|
| Space / ❚❚ button | play / pause |
| `s` / speed button | cycle 1× → 2× → 4× → 8× |
| slider, ← / → | seek by event |
| `r` | restart |
| `e` | skip to end |

Long waits from the original recording are compressed so the demo stays snappy, but
the status spinner still shows the *real* elapsed time from the session, so the
audience sees how long each step actually took.

## How it works

Claude Code stores every session as a JSONL transcript under `~/.claude/projects/`.
`scripts/replay.py` resolves a session ID to its transcript, parses it into replay
events (prompts, assistant text, thinking, tool calls with results, interrupts), and
bakes them into `scripts/template.html` — a small dependency-free player that renders
a terminal window and animates the events with the original session's pacing.

Subagent chatter, meta entries, and file-history snapshots are filtered out; long tool
outputs are truncated the same way Claude Code's UI does (`… +N lines`); ANSI escape
codes are stripped.

## Privacy note

The generated HTML **embeds the session content**: your prompts, file paths, commands,
and tool output. Review before sharing publicly, just as you would a screen recording.

## License

MIT
