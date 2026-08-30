"""Install EVO MK2 as a true Windows Service (SCM) or logon Scheduled Task.

Features:
- Real SCM Windows Service registration via sc.exe with automatic crash recovery
- Scheduled Task option for user-session desktop audio / microphone access
- Foreground and background run modes
"""
import argparse
import subprocess
import sys
from pathlib import Path

SERVICE_NAME = "EVO_MK2"
SERVICE_DISPLAY = "EVO MK2 Ambient Assistant"
TASK_NAME = "EVO_MK2_Kernel"
REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_EXE = sys.executable
RUNNER_BAT = REPO_ROOT / "run_evo.bat"


def create_runner_script() -> Path:
    content = f"""@echo off
cd /d "{REPO_ROOT}"
:loop
echo [%date% %time%] Starting EVO MK2 Kernel...
"{PYTHON_EXE}" -m mk2.kernel --foreground
echo [%date% %time%] EVO MK2 Kernel exited with code %ERRORLEVEL%. Restarting in 5s...
timeout /t 5 /nobreak >nul
goto loop
"""
    RUNNER_BAT.write_text(content, encoding="utf-8")
    return RUNNER_BAT


# ---------------- Windows Service (SCM) ----------------

def install_service() -> bool:
    """Register EVO MK2 in the Windows Service Control Manager with auto-recovery."""
    binpath = f'"{PYTHON_EXE}" -m mk2.kernel --service'
    cmd = [
        "sc.exe", "create", SERVICE_NAME,
        f"binPath= {binpath}",
        "start= auto",
        f"DisplayName= {SERVICE_DISPLAY}",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            print(f"SUCCESS: Windows Service '{SERVICE_NAME}' created.")
            # Configure automatic restart on failure
            rec_cmd = ["sc.exe", "failure", SERVICE_NAME, "reset=", "86400", "actions=", "restart/5000/restart/5000/restart/5000"]
            subprocess.run(rec_cmd, capture_output=True, text=True)
            # Set description
            desc_cmd = ["sc.exe", "description", SERVICE_NAME, "EVO MK2 Ambient AI Assistant Kernel Daemon"]
            subprocess.run(desc_cmd, capture_output=True, text=True)
            return True
        else:
            print(f"FAILED: {res.stderr.strip() or res.stdout.strip()}")
            return False
    except Exception as exc:
        print(f"ERROR: {exc}")
        return False


def uninstall_service() -> bool:
    try:
        subprocess.run(["sc.exe", "stop", SERVICE_NAME], capture_output=True)
        res = subprocess.run(["sc.exe", "delete", SERVICE_NAME], capture_output=True, text=True)
        if res.returncode == 0:
            print(f"SUCCESS: Windows Service '{SERVICE_NAME}' deleted.")
            return True
        else:
            print(f"FAILED: {res.stderr.strip() or res.stdout.strip()}")
            return False
    except Exception as exc:
        print(f"ERROR: {exc}")
        return False


def start_service() -> None:
    res = subprocess.run(["sc.exe", "start", SERVICE_NAME], capture_output=True, text=True)
    print(res.stdout or res.stderr)


def stop_service() -> None:
    res = subprocess.run(["sc.exe", "stop", SERVICE_NAME], capture_output=True, text=True)
    print(res.stdout or res.stderr)


def service_status() -> None:
    res = subprocess.run(["sc.exe", "query", SERVICE_NAME], capture_output=True, text=True)
    print(res.stdout or res.stderr)


# ---------------- Scheduled Task (User Session with Audio/Mic) ----------------

def install_task() -> bool:
    bat_path = create_runner_script()
    cmd = [
        "schtasks", "/create",
        "/tn", TASK_NAME,
        "/tr", f'"{bat_path}"',
        "/sc", "onlogon",
        "/delay", "0000:30",
        "/rl", "highest",
        "/f"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            print(f"SUCCESS: Scheduled Task '{TASK_NAME}' installed successfully.")
            return True
        else:
            print(f"FAILED: {res.stderr.strip() or res.stdout.strip()}")
            return False
    except Exception as exc:
        print(f"ERROR: {exc}")
        return False


def uninstall_task() -> bool:
    cmd = ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            print(f"SUCCESS: Scheduled Task '{TASK_NAME}' removed.")
            return True
        else:
            print(f"FAILED: {res.stderr.strip() or res.stdout.strip()}")
            return False
    except Exception as exc:
        print(f"ERROR: {exc}")
        return False


def main():
    parser = argparse.ArgumentParser(description="EVO MK2 Ambient Service & Autostart Manager")
    parser.add_argument("--install-service", action="store_true", help="Register as Windows Service (SCM)")
    parser.add_argument("--uninstall-service", action="store_true", help="Delete Windows Service (SCM)")
    parser.add_argument("--start-service", action="store_true", help="Start Windows Service")
    parser.add_argument("--stop-service", action="store_true", help="Stop Windows Service")
    parser.add_argument("--service-status", action="store_true", help="Query Windows Service status")
    parser.add_argument("--install-task", action="store_true", help="Install logon Scheduled Task")
    parser.add_argument("--uninstall-task", action="store_true", help="Delete logon Scheduled Task")
    args = parser.parse_args()

    if args.install_service:
        install_service()
    elif args.uninstall_service:
        uninstall_service()
    elif args.start_service:
        start_service()
    elif args.stop_service:
        stop_service()
    elif args.service_status:
        service_status()
    elif args.install_task:
        install_task()
    elif args.uninstall_task:
        uninstall_task()
    else:
        service_status()


if __name__ == "__main__":
    main()
