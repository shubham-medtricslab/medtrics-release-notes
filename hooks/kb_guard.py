"""kb_guard — PreToolUse hook for the /kb-articles Chrome agents.

kb-capturer: denies typing, key presses, drags and form_input — directly or
inside a browser_batch.
kb-data-maker (hook input `agent_type` contains "kb-data-maker"): may type and
use form_input, and press only the Tab and Escape keys (computer key with text
exactly "Tab" or "Escape"); every other key (Enter included) and every drag is
denied.
Both agents (0.27.0): `type` text holding a newline, carriage return or tab is
denied (it would submit or move focus); `javascript_tool` text must start with the
marker `/*KB*/` that every script the plugin generates carries; `navigate` must
stay on the demo host (scripts/kb/kb_routes.json demo.base_url, else the literal
below) or be a path starting with a single "/", and a path whose text says
delete / remove / logout / signout / portal_change is refused unless it is exactly
one of the configured demo.portal_entry values.
Every other caller, and any error at all, is allowed silently, so the plugin
never gets in the way of other Chrome use. Reads stdin and the routes file, writes
nothing to disk. Stdlib only.
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

DENY_ACTIONS = ("type", "key", "left_click_drag")
MAKER_DENY_ACTIONS = ("key", "left_click_drag")
REASON = ("KB_GUARD: the KB capturer may only click the point kbclick returned; "
          "typing, key presses, drags and form input are not allowed.")
MAKER_KEYS = ("Tab", "Escape")
JS_MARKER = "/*KB*/"
DEMO_HOST = "medtrics.medtricslab.com"
ROUTES = Path(__file__).resolve().parent.parent / "scripts" / "kb" / "kb_routes.json"
DANGER_RE = re.compile(r"delete|remove|logout|signout|portal_change", re.I)
CONTROL_RE = re.compile(r"[\n\r\t]")
JS_REASON = "KB_GUARD: javascript_tool text must start with the /*KB*/ marker of the plugin's own scripts."
TYPE_REASON = "KB_GUARD: typed text may not contain a newline, carriage return or tab."
NAV_REASON = ("KB_GUARD: navigate only to the demo site (a path, or the demo host), and never to a "
              "delete / remove / logout / portal-switch URL other than the configured portal entry.")
MAKER_REASON = ("KB_GUARD: the KB data maker may type field values and press Tab or Escape only; "
                "other key presses (Enter included) and drags are not allowed.")


def _routes():
    """(demo host, set of exact portal_entry paths) from kb_routes.json; the literal
    fallbacks when it cannot be read."""
    host, entries = DEMO_HOST, set()
    try:
        demo = json.loads(ROUTES.read_text()).get("demo") or {}
        host = urlsplit(str(demo.get("base_url") or "")).hostname or DEMO_HOST
        entries = {str(v) for v in (demo.get("portal_entry") or {}).values()}
    except Exception:
        pass
    return host, entries


def _bad_navigate(url):
    """True when this navigate url is off the demo site or a dangerous demo path."""
    host, entries = _routes()
    u = str(url or "")
    if not u or u != u.strip() or re.search(r"[\x00-\x1f\x7f]", u):
        return True                     # empty, padded or holding a control character
    if u.startswith("/") and not u.startswith("//") and not u.startswith("/\\"):
        path = u
    else:
        sp = urlsplit(u if re.match(r"^[a-z][a-z0-9+.-]*://", u, re.I) else "https://" + u.lstrip("/"))
        if sp.scheme.lower() not in ("http", "https") or (sp.hostname or "").lower() != host.lower():
            return True
        path = (sp.path or "/") + ("?" + sp.query if sp.query else "")
    path = path.split("#", 1)[0]
    if DANGER_RE.search(unquote(path)):
        return path not in entries
    return False


def _deny(tool, tool_input, maker=False):
    """The deny reason for one tool call (a browser_batch is checked action by action), or ""."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    own = MAKER_REASON if maker else REASON
    if tool.endswith("javascript_tool"):
        return "" if str(inp.get("text") or "").startswith(JS_MARKER) else JS_REASON
    if tool.endswith("navigate"):
        return NAV_REASON if _bad_navigate(inp.get("url")) else ""
    if tool.endswith("form_input"):
        return "" if maker else own
    if tool.endswith("computer"):
        action = str(inp.get("action") or "")
        if action == "type" and CONTROL_RE.search(str(inp.get("text") or "")):
            return TYPE_REASON
        if maker:
            if action == "key":
                return own if str(inp.get("text") or "") not in MAKER_KEYS else ""
            return own if action in MAKER_DENY_ACTIONS else ""
        return own if action in DENY_ACTIONS else ""
    if tool.endswith("browser_batch"):
        for a in inp.get("actions") or []:
            if isinstance(a, dict):
                why = _deny(str(a.get("name") or ""), a.get("input"), maker)
                if why:
                    return why
    return ""


def decide(data):
    """The deny reason for this hook input, or None to allow."""
    if not isinstance(data, dict):
        return None
    agent = str(data.get("agent_type") or "")
    tool, tool_input = str(data.get("tool_name") or ""), data.get("tool_input")
    if "kb-capturer" in agent:
        return _deny(tool, tool_input) or None
    if "kb-data-maker" in agent:
        return _deny(tool, tool_input, maker=True) or None
    return None


def main():
    try:
        reason = decide(json.load(sys.stdin))
    except Exception:
        return 0
    if reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
