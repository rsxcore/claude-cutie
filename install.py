"""Install (or remove) the claude-cutie hooks in ~/.claude/settings.json.

    python install.py              # add hooks
    python install.py --uninstall  # remove hooks

A backup of the previous settings is written to settings.json.bak.
"""
import json, shutil, sys
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
HOOK = Path(__file__).resolve().parent / "hook.py"
PYTHON = Path(sys.executable).as_posix()

EVENTS = {
    "SessionStart": "start",        # also launches the pet
    "UserPromptSubmit": "thinking",
    "PreToolUse": "tool",
    "PostToolUse": "thinking",
    "Notification": "waiting",
    "Stop": "done",
}


def ours(entry):
    return HOOK.as_posix() in json.dumps(entry)


def main():
    uninstall = "--uninstall" in sys.argv
    settings = {}
    if SETTINGS.exists():
        shutil.copy(SETTINGS, SETTINGS.with_suffix(".json.bak"))
        settings = json.loads(SETTINGS.read_text(encoding="utf-8") or "{}")

    hooks = settings.setdefault("hooks", {})
    for event, arg in EVENTS.items():
        entries = [e for e in hooks.get(event, []) if not ours(e)]
        if not uninstall:
            hook = {"type": "command", "command": f'"{PYTHON}" "{HOOK.as_posix()}" {arg}'}
            entries.append({"matcher": "*", "hooks": [hook]} if "Tool" in event else {"hooks": [hook]})
        if entries:
            hooks[event] = entries
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks")

    SETTINGS.parent.mkdir(exist_ok=True)
    SETTINGS.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(("Removed hooks from " if uninstall else "Installed hooks into ") + str(SETTINGS))
    if not uninstall:
        print("Start a new Claude Code session and Clawd will show up.")


if __name__ == "__main__":
    main()
