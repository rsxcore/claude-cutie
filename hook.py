"""Claude Code hook: writes the current activity to ~/.claude/pet_state.json
and launches the pet on SessionStart if it isn't running yet."""
import json, socket, subprocess, sys, time
from pathlib import Path

STATE = Path.home() / ".claude" / "pet_state.json"
PET = Path(__file__).resolve().parent / "pet.py"
LOCK_PORT = 47321

TOOL_STATES = {
    "Edit": "coding", "Write": "writing", "MultiEdit": "coding", "NotebookEdit": "coding",
    "Read": "reading", "Grep": "reading", "Glob": "reading", "WebFetch": "reading", "WebSearch": "reading",
    "Bash": "running", "PowerShell": "running", "Agent": "running", "Task": "running",
}


def pet_running():
    try:
        socket.create_connection(("127.0.0.1", LOCK_PORT), timeout=0.3).close()
        return True
    except OSError:
        return False


def launch_pet():
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(pythonw if pythonw.exists() else exe), str(PET)], cwd=str(PET.parent),
                     creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else "idle"
    try:  # Claude Code sends UTF-8; don't rely on the Windows console codepage
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    except Exception:
        data = {}
    try:
        prev = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        prev = {}

    prompt = " ".join(str(data.get("prompt") or prev.get("prompt") or "").split())
    detail, state = "", event
    if event == "start":
        state = "idle"
        if not pet_running():
            launch_pet()
    elif event == "waiting":
        # Notification also fires as an "idle" reminder after Claude finished; that isn't a request
        kind = str(data.get("notification_type") or "")
        msg = str(data.get("message") or "").lower()
        if kind == "idle_prompt" or "waiting for your input" in msg:
            state = "idle"
    elif event == "tool":
        state = TOOL_STATES.get(data.get("tool_name", ""), "thinking")
        ti = data.get("tool_input") or {}
        path = ti.get("file_path") or ti.get("notebook_path")
        detail = Path(path).name if path else str(ti.get("description") or ti.get("pattern") or ti.get("query") or "")

    STATE.write_text(json.dumps({"state": state, "t": time.time(), "prompt": prompt[:200],
                                 "detail": " ".join(detail.split())[:60]}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
