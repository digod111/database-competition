import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(args):
    print("+", " ".join(str(item) for item in args))
    subprocess.run([sys.executable, *[str(item) for item in args]], check=True, cwd=ROOT)


def main():
    run([ROOT / "scripts" / "powercontext_ctl.py", "reset"])
    run([ROOT / "data" / "init_db.py"])
    try:
        with urllib.request.urlopen("http://127.0.0.1:8010/health", timeout=3) as resp:
            print("backend health:", resp.status)
    except Exception:
        print("warning: backend is not reachable on 127.0.0.1:8010; start it with backend/start.sh")
    print("reset done: seekdb reseeded and PowerContext memory home replaced.")
    print("next: open the Dify app and start a NEW conversation with player_id player_01.")


if __name__ == "__main__":
    main()
