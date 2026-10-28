"""kb_guard — PreToolUse hook for the /kb-articles capturer.

Denies typing, key presses, drags and form_input — directly or inside a
browser_batch — but only when the call comes
from the kb-capturer subagent (hook input `agent_type` contains "kb-capturer").
Every other caller, and any error at all, is allowed silently, so the plugin
never gets in the way of other Chrome use. Reads stdin, writes nothing to disk.
Stdlib only.
"""
import json
import sys

DENY_ACTIONS = ("type", "key", "left_click_drag")
REASON = ("KB_GUARD: the KB capturer may only click the point kbclick returned; "
          "typing, key presses, drags and form input are not allowed.")


def _blocked(tool, tool_input):
    if tool.endswith("form_input"):
        return True
    if tool.endswith("computer"):
        return str((tool_input or {}).get("action") or "") in DENY_ACTIONS
    if tool.endswith("browser_batch"):
        return any(isinstance(a, dict) and _blocked(str(a.get("name") or ""), a.get("input"))
                   for a in (tool_input or {}).get("actions") or [])
    return False


def decide(data):
    """The deny reason for this hook input, or None to allow."""
    if not isinstance(data, dict) or "kb-capturer" not in str(data.get("agent_type") or ""):
        return None
    return REASON if _blocked(str(data.get("tool_name") or ""), data.get("tool_input")) else None


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
