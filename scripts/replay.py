#!/usr/bin/env python3
"""Generate a self-contained HTML "terminal recording" replay of a Claude Code session.

Takes a session ID (or a path to a session .jsonl), parses the transcript, and
writes a single HTML file that replays the session inside a fake terminal window:
user prompts get typed in, Claude "thinks" with a spinner, tool calls run and
print their output — just like watching the real thing, but on your schedule.

No dependencies beyond the Python standard library.

Usage:
    replay.py <sessionId|path.jsonl> [-o out.html] [--title "My demo"]
    replay.py --list [N]        # list recent sessions to pick from
"""

import argparse
import difflib
import glob
import html
import json
import os
import re
import sys
from datetime import datetime, timezone

PROJECTS_DIR = os.path.expanduser(os.environ.get("CLAUDE_PROJECTS_DIR", "~/.claude/projects"))

MAX_RESULT_LINES = 10
MAX_RESULT_CHARS = 1500
MAX_DIFF_LINES = 14
MAX_THINKING_CHARS = 600
MAX_TEXT_CHARS = 6000

# Claude Code display names for tools
TOOL_DISPLAY = {
    "Edit": "Update",
    "MultiEdit": "Update",
    "Grep": "Search",
    "Glob": "Search",
    "WebFetch": "Fetch",
    "WebSearch": "Web Search",
    "TodoWrite": "Update Todos",
    "Agent": "Task",
}


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def find_session_file(session_id):
    """Resolve a session id (or unique prefix) to a transcript path."""
    if os.path.isfile(session_id):
        return session_id
    matches = glob.glob(os.path.join(PROJECTS_DIR, "*", session_id + "*.jsonl"))
    # Prefer exact matches over prefix matches
    exact = [m for m in matches if os.path.basename(m) == session_id + ".jsonl"]
    if exact:
        matches = exact
    if not matches:
        sys.exit(f"error: no session matching {session_id!r} under {PROJECTS_DIR}\n"
                 f"hint: run with --list to see recent sessions")
    if len(matches) > 1:
        listing = "\n  ".join(matches)
        sys.exit(f"error: session id {session_id!r} is ambiguous:\n  {listing}")
    return matches[0]


def list_sessions(limit):
    files = glob.glob(os.path.join(PROJECTS_DIR, "*", "*.jsonl"))
    files.sort(key=os.path.getmtime, reverse=True)
    rows = []
    for f in files[:limit]:
        sid = os.path.basename(f)[:-6]
        project = os.path.basename(os.path.dirname(f))
        title = ""
        try:
            with open(f, errors="replace") as fh:
                for line in fh:
                    if '"ai-title"' not in line and '"aiTitle"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if o.get("type") == "ai-title":
                        title = o.get("aiTitle", "")
        except OSError:
            pass
        mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
        rows.append((mtime, sid, project, title))
    if not rows:
        print(f"no sessions found under {PROJECTS_DIR}")
        return
    for mtime, sid, project, title in rows:
        print(f"{mtime}  {sid}  {project}  {title}")


def short_path(path, cwd):
    if not isinstance(path, str):
        return str(path)
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        return path[len(cwd.rstrip("/")) + 1:]
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def one_line(s, limit=110):
    s = " ".join(str(s).split())
    return s[: limit - 1] + "…" if len(s) > limit else s


def tool_summary(name, inp, cwd):
    """Build the `ToolName(args)` header text the way Claude Code renders it."""
    if not isinstance(inp, dict):
        inp = {}
    display = TOOL_DISPLAY.get(name, name)
    if name.startswith("mcp__"):
        parts = name.split("__")
        display = parts[-1] + " (MCP)"
    if name == "Bash":
        arg = one_line(inp.get("command", ""))
    elif name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        arg = short_path(inp.get("file_path") or inp.get("notebook_path") or "", cwd)
    elif name in ("Grep", "Glob"):
        arg = 'pattern: "%s"' % one_line(inp.get("pattern", ""), 60)
        if inp.get("path"):
            arg += ", path: " + short_path(inp["path"], cwd)
    elif name in ("Task", "Agent"):
        arg = one_line(inp.get("description") or inp.get("prompt", ""), 80)
    elif name == "WebFetch":
        arg = one_line(inp.get("url", ""), 90)
    elif name == "WebSearch":
        arg = one_line(inp.get("query", ""), 90)
    elif name == "Skill":
        arg = one_line(inp.get("skill", ""), 60)
    elif name == "TaskCreate":
        arg = one_line(inp.get("subject", ""), 80)
    elif name in ("TaskUpdate", "TaskGet", "TaskStop", "TaskOutput"):
        arg = "#" + str(inp.get("taskId", "?"))
        if inp.get("status"):
            arg += " → " + str(inp["status"])
        elif inp.get("subject"):
            arg += " " + one_line(inp["subject"], 60)
    elif name == "AskUserQuestion":
        qs = inp.get("questions") or []
        arg = one_line(qs[0].get("question", "") if qs and isinstance(qs[0], dict) else "", 90)
    elif name == "Workflow":
        arg = inp.get("name") or ("script" if inp.get("script") or inp.get("scriptPath") else "")
    elif name == "TodoWrite":
        arg = ""
    else:
        keys = ("description", "query", "prompt", "file_path", "path", "url", "title")
        arg = next((one_line(inp[k], 80) for k in keys if inp.get(k)), "")
        if not arg and inp:
            arg = one_line(json.dumps(inp, ensure_ascii=False), 80)
    return f"{display}({arg})" if arg else display


def truncate_block(text, max_lines=MAX_RESULT_LINES, max_chars=MAX_RESULT_CHARS):
    """Trim long output; return (kept_text, hidden_line_count)."""
    text = clean_text(text).rstrip("\n")
    if len(text) > max_chars:
        text = text[:max_chars]
    lines = text.split("\n")
    hidden = 0
    if len(lines) > max_lines:
        hidden = len(lines) - max_lines
        lines = lines[:max_lines]
    return "\n".join(lines), hidden


def diff_lines(old, new):
    """Changed lines between two strings as [sign, text] pairs (like CC's edit view)."""
    a, b = old.split("\n"), new.split("\n")
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag in ("replace", "delete"):
            out += [["-", clean_text(l)[:160]] for l in a[i1:i2]]
        if tag in ("replace", "insert"):
            out += [["+", clean_text(l)[:160]] for l in b[j1:j2]]
    return out


def attach_diff(ev, name, inp, cwd):
    """For file-editing tools, capture a red/green diff from the tool input."""
    if not isinstance(inp, dict):
        return
    dl = []
    if name == "Edit":
        dl = diff_lines(inp.get("old_string") or "", inp.get("new_string") or "")
    elif name == "MultiEdit":
        for e in inp.get("edits") or []:
            if isinstance(e, dict):
                dl += diff_lines(e.get("old_string") or "", e.get("new_string") or "")
    elif name in ("Write", "NotebookEdit"):
        content = inp.get("content") or inp.get("new_source") or ""
        if content:
            dl = [["+", clean_text(l)[:160]] for l in content.split("\n")]
    if not dl:
        return
    adds = sum(1 for d in dl if d[0] == "+")
    rems = len(dl) - adds
    path = short_path(inp.get("file_path") or inp.get("notebook_path") or "file", cwd)
    if name == "Write":
        ev["dsum"] = f"Wrote {adds} line{'s' if adds != 1 else ''} to {path}"
    else:
        ev["dsum"] = (f"Updated {path} with {adds} addition{'s' if adds != 1 else ''}"
                      f" and {rems} removal{'s' if rems != 1 else ''}")
    ev["diff"] = dl[:MAX_DIFF_LINES]
    ev["dhidden"] = max(0, len(dl) - MAX_DIFF_LINES)


def result_text(content):
    """tool_result content may be a string or a list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "image":
                src = b.get("source") or {}
                kb = round(len(src.get("data", "")) * 3 / 4 / 1024)
                media = src.get("media_type", "image")
                parts.append(f"[Image: {media}{f' · {kb} KB' if kb else ''}]")
        return "\n".join(parts)
    return ""


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(\x07|\x1b\\)?|[\x00-\x08\x0b-\x1f\x7f]")


def clean_text(s):
    """Strip ANSI escapes and control chars that would render as tofu in HTML."""
    return ANSI_RE.sub("", s or "")


COMMAND_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
BASH_INPUT_RE = re.compile(r"<bash-input>(.*?)</bash-input>", re.S)
BASH_STDOUT_RE = re.compile(r"<bash-stdout>(.*?)</bash-stdout>", re.S)
BASH_STDERR_RE = re.compile(r"<bash-stderr>(.*?)</bash-stderr>", re.S)
SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
TEAMMATE_RE = re.compile(r"<teammate-message\b([^>]*)>(.*?)</teammate-message>", re.S)
TEAMMATE_ID_RE = re.compile(r"(?:teammate|teamate)[_-]?id\s*=\s*[\"']?([^\"'\s>]+)")
NOTIF_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.S)


def user_text_events(text, ts):
    """Classify one user-entry text into replay events.

    Claude Code injects harness messages (task notifications, teammate messages,
    system reminders, slash-command echoes, `!` shell passthrough) as user-type
    entries; replaying them verbatim would show raw XML being "typed" by the user.
    """
    text = (text or "").strip()
    if not text:
        return []
    if "<local-command-stdout>" in text or "<local-command-caveat>" in text:
        return []
    text = SYSTEM_REMINDER_RE.sub("", text).strip()
    if not text:
        return []
    if text.startswith("[Request interrupted"):
        return [{"k": "int", "ts": ts}]

    m = BASH_INPUT_RE.search(text)
    if m:
        return [{"k": "user", "text": "! " + m.group(1).strip(), "ts": ts}]
    if "<bash-stdout>" in text or "<bash-stderr>" in text:
        so = BASH_STDOUT_RE.search(text)
        se = BASH_STDERR_RE.search(text)
        parts = [x.group(1).strip() for x in (so, se) if x and x.group(1).strip()]
        if not parts:
            return []
        res, hidden = truncate_block("\n".join(parts))
        err = bool(se and se.group(1).strip()) and not (so and so.group(1).strip())
        return [{"k": "sh", "res": res, "hidden": hidden, "err": err, "ts": ts}]

    m = COMMAND_RE.search(text)
    if m:
        args = COMMAND_ARGS_RE.search(text)
        cmd = (m.group(1).strip() + " " + (args.group(1).strip() if args else "")).strip()
        return [{"k": "user", "text": cmd, "ts": ts}] if cmd else []

    events = []
    for m in TEAMMATE_RE.finditer(text):
        sender = TEAMMATE_ID_RE.search(m.group(1))
        events.append({"k": "team", "from": sender.group(1) if sender else "teammate",
                       "text": clean_text(m.group(2).strip())[:MAX_TEXT_CHARS], "ts": ts})
    if events:
        return events
    if "<task-notification>" in text:
        m = NOTIF_SUMMARY_RE.search(text)
        summary = clean_text(m.group(1).strip()) if m else "Background task completed"
        return [{"k": "notice", "text": one_line(summary, 160), "ts": ts}]

    return [{"k": "user", "text": clean_text(text)[:MAX_TEXT_CHARS], "ts": ts}]


def parse_session(path):
    """Parse a transcript .jsonl into replay events + session metadata."""
    events = []
    tools_by_id = {}
    meta = {"sessionId": os.path.basename(path)[:-6], "cwd": "", "model": "",
            "gitBranch": "", "title": "", "startTs": None}

    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = o.get("type")

            if t == "ai-title":
                meta["title"] = o.get("aiTitle") or meta["title"]
                continue
            if t == "system" and o.get("subtype") == "away_summary" and o.get("content"):
                events.append({"k": "notice",
                               "text": "While you were away: " + one_line(clean_text(o["content"]), 220),
                               "ts": parse_ts(o.get("timestamp"))})
                continue
            if t not in ("user", "assistant"):
                continue
            if o.get("isSidechain"):
                continue  # subagent traffic; the main thread is the story

            ts = parse_ts(o.get("timestamp"))
            if meta["startTs"] is None and ts:
                meta["startTs"] = ts
            if not meta["cwd"] and o.get("cwd"):
                meta["cwd"] = o["cwd"]
            if not meta["gitBranch"] and o.get("gitBranch"):
                meta["gitBranch"] = o["gitBranch"]

            msg = o.get("message") or {}
            content = msg.get("content")

            if t == "user":
                if o.get("isMeta"):
                    continue
                if isinstance(content, str):
                    events.extend(user_text_events(content, ts))
                elif isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_result":
                            ev = tools_by_id.get(b.get("tool_use_id"))
                            if ev is not None:
                                res, hidden = truncate_block(result_text(b.get("content")))
                                ev["res"] = res
                                ev["hidden"] = hidden
                                ev["err"] = bool(b.get("is_error"))
                                if ts and ev["ts"]:
                                    ev["dur"] = max(0, ts - ev["ts"])
                                if ev["err"]:
                                    # failed edits show the error, not a diff of what didn't happen
                                    ev.pop("diff", None), ev.pop("dhidden", None), ev.pop("dsum", None)
                                elif ev.get("diff"):
                                    ev["res"] = ev.pop("dsum", "Updated file")
                                    ev["hidden"] = 0
                        elif b.get("type") == "text":
                            for e in user_text_events(b.get("text", ""), ts):
                                # block texts are usually harness-injected; only keep
                                # ones we classified or that read as a real prompt
                                if e["k"] != "user" or not e["text"].startswith("<"):
                                    events.append(e)

            elif t == "assistant":
                if msg.get("model"):
                    meta["model"] = msg["model"]
                for b in content or []:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text":
                        text = b.get("text", "").strip()
                        if text:
                            events.append({"k": "a", "text": text[:MAX_TEXT_CHARS], "ts": ts})
                    elif bt == "thinking":
                        think = (b.get("thinking") or "").strip()
                        if think:
                            events.append({"k": "think", "text": think[:MAX_THINKING_CHARS], "ts": ts})
                    elif bt == "tool_use":
                        ev = {"k": "tool", "name": b.get("name", "Tool"),
                              "summary": tool_summary(b.get("name", ""), b.get("input"), meta["cwd"]),
                              "res": "", "hidden": 0, "err": False, "dur": 1.0, "ts": ts}
                        attach_diff(ev, b.get("name", ""), b.get("input"), meta["cwd"])
                        tools_by_id[b.get("id")] = ev
                        events.append(ev)

    # Collapse consecutive duplicate user events (retries after interrupts etc.)
    deduped = []
    for ev in events:
        if deduped and ev["k"] == "user" and deduped[-1]["k"] == "user" \
                and deduped[-1]["text"] == ev["text"]:
            continue
        deduped.append(ev)
    return deduped, meta


def build_html(events, meta, title):
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html")
    with open(template_path) as fh:
        template = fh.read()

    def embed(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    started = ""
    if meta["startTs"]:
        started = datetime.fromtimestamp(meta["startTs"], tz=timezone.utc).astimezone().strftime("%b %d, %Y")

    page_title = title or meta["title"] or f"Claude Code session {meta['sessionId'][:8]}"
    payload = {
        "cwd": meta["cwd"] or "~",
        "model": meta["model"] or "claude",
        "branch": meta["gitBranch"] or "",
        "sessionId": meta["sessionId"],
        "started": started,
        "title": page_title,
    }
    return (template
            .replace("__PAGE_TITLE__", html.escape(page_title))
            .replace("__META_JSON__", embed(payload))
            .replace("__EVENTS_JSON__", embed(events)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session", nargs="?", help="session id (or unique prefix), or path to a session .jsonl")
    ap.add_argument("-o", "--out", help="output HTML path (default: <sessionId>-replay.html)")
    ap.add_argument("--title", help="page/window title override")
    ap.add_argument("--list", nargs="?", const=15, type=int, metavar="N",
                    help="list the N most recent sessions and exit (default 15)")
    args = ap.parse_args()

    if args.list is not None:
        list_sessions(args.list)
        return
    if not args.session:
        ap.error("provide a session id, or use --list to browse recent sessions")

    path = find_session_file(args.session)
    events, meta = parse_session(path)
    if not events:
        sys.exit(f"error: no replayable messages found in {path}")

    out = args.out or f"{meta['sessionId'][:8]}-replay.html"
    with open(out, "w") as fh:
        fh.write(build_html(events, meta, args.title))
    n_user = sum(1 for e in events if e["k"] == "user")
    n_tool = sum(1 for e in events if e["k"] == "tool")
    print(f"wrote {out} ({len(events)} events: {n_user} prompts, {n_tool} tool calls)")
    print(f"source: {path}")


if __name__ == "__main__":
    main()
