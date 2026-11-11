import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def find_exe() -> str:
    env = os.getenv("POWERCONTEXT_EXE")
    if env and Path(env).exists():
        return env
    found = shutil.which("powercontext")
    if found:
        return found
    candidate = Path(sys.executable).with_name("powercontext.exe" if os.name == "nt" else "powercontext")
    if candidate.exists():
        return str(candidate)
    return "powercontext"


def health(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def start(home: Path, port: int, token: str, log: Path) -> int:
    home = home.resolve()
    log = log.resolve()
    home.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "POWERCONTEXT_HOME": str(home),
            "POWERCONTEXT_SERVER_HTTP_HOST": "0.0.0.0",
            "POWERCONTEXT_SERVER_HTTP_PORT": str(port),
            "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
            "POWERCONTEXT_SERVER_AUTH_TOKEN": token,
            "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "false",
        }
    )
    handle = log.open("a", encoding="utf-8")
    handle.write(f"\n===== start {datetime.now().isoformat()} =====\n")
    handle.flush()
    kwargs = {"env": env, "stdout": handle, "stderr": subprocess.STDOUT, "cwd": str(home)}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([find_exe(), "server", "run"], **kwargs)
    (home / "pc.pid").write_text(str(proc.pid), encoding="utf-8")
    for _ in range(120):
        if health(port):
            print(f"PowerContext ready on :{port} (pid {proc.pid}) home={home}")
            return proc.pid
        if proc.poll() is not None:
            raise RuntimeError(f"PowerContext exited early; see {log}")
        time.sleep(1)
    raise RuntimeError(f"PowerContext not ready in 120s; see {log}")


def stop(home: Path, port: int) -> None:
    home = home.resolve()
    pid_file = home / "pc.pid"
    pid = None
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = None
    if pid:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            try:
                os.killpg(os.getpgid(pid), 15)
            except ProcessLookupError:
                pass
    for _ in range(40):
        if not health(port):
            break
        time.sleep(0.5)
    pid_file.unlink(missing_ok=True)
    print(f"PowerContext stopped (port {port})")


def reset(home: Path, port: int, token: str, log: Path) -> None:
    home = home.resolve()
    stop(home, port)
    if home.exists():
        backup = home.with_name(home.name + ".bak." + datetime.now().strftime("%Y%m%d%H%M%S"))
        shutil.move(str(home), str(backup))
        print(f"old PowerContext home moved to {backup}")
    start(home, port, token, log)


def main():
    parser = argparse.ArgumentParser(description="PowerContext local controller")
    parser.add_argument("action", choices=["start", "stop", "reset", "status"])
    parser.add_argument("--home", default=str(BASE_DIR / ".runtime" / "powercontext"))
    parser.add_argument("--port", type=int, default=18082)
    parser.add_argument("--token", default="tavern-demo-token")
    parser.add_argument("--log", default=str(BASE_DIR / ".runtime" / "powercontext.log"))
    args = parser.parse_args()
    home = Path(args.home)
    log = Path(args.log)
    if args.action == "start":
        start(home, args.port, args.token, log)
    elif args.action == "stop":
        stop(home, args.port)
    elif args.action == "reset":
        reset(home, args.port, args.token, log)
    else:
        print(f"healthy={health(args.port)} port={args.port} home={home.resolve()}")


if __name__ == "__main__":
    main()
