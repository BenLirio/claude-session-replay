---
name: session-replay
description: Generate a self-contained HTML "terminal recording" that replays a past Claude Code session — user prompts typed in live, thinking spinners, tool calls with output — like a screen recording of the real thing. Use this whenever the user wants to demo, share, present, record, or replay a Claude Code session or chat, wants a "video" of Claude working without waiting for it live, or asks to turn a session/conversation/transcript into something watchable or shareable.
---

# Session Replay

Turn a Claude Code session transcript into a single HTML file that looks and behaves
like a terminal window replaying the session in real time — with play/pause, seeking,
and speed controls. Perfect for demos and presentations where waiting for a live
Claude run is too slow or unpredictable.

## Workflow

1. **Identify the session.** If the user gave a session ID, use it directly (unique
   prefixes work). If they didn't, list recent sessions and pick or ask:

   ```bash
   python3 scripts/replay.py --list 15
   ```

   Each row shows: modified time, session ID, project directory, and AI-generated title.
   If the user says "this session" or "the current session", the current session's ID is
   the basename of the transcript file for this conversation (also often visible in
   context). If the user describes the session ("the one where we set up fastlane"),
   match against the titles in the list.

2. **Generate the replay:**

   ```bash
   python3 scripts/replay.py <sessionId> -o <descriptive-name>-replay.html
   ```

   Useful flags:
   - `-o PATH` — output file (default: `<sessionId8>-replay.html` in cwd)
   - `--title "..."` — override the page/window caption
   - A path to a `.jsonl` transcript also works instead of a session ID, so
     transcripts copied from another machine replay fine.

3. **Report the result.** Tell the user the output path and the event count the script
   printed. Suggest opening it in a browser. The file is fully self-contained (no
   network, no dependencies) — it can be emailed, dropped in a repo, or hosted anywhere
   as static content.

4. **Mind privacy.** The HTML embeds the session transcript: prompts, file paths, tool
   commands, and tool output. If the session touched secrets or private code, warn the
   user before they share the file publicly.

## Viewer controls (tell the user)

- Navigation is by **user prompt**: ‹/› buttons or ←/→ jump between prompts, the
  slider snaps to prompts, and the counter shows `prompt 3/10`. Skipping pauses with
  the prompt staged in the input box — press play to send it and show the response
- Space / button: play–pause · `s` or button: speed 1×/2×/4×/8× · `r`: restart ·
  `e`: skip to end
- Long waits from the recording are compressed so the demo stays snappy, while the
  spinner still reports the *real* elapsed time from the original session.

## Requirements

Python 3.8+ only (standard library). Transcripts are read from `~/.claude/projects/`
(override with `CLAUDE_PROJECTS_DIR`).
