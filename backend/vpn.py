"""
NordVPN control via AppleScript (macOS only).

connect()    — open NordVPN and connect, wait for tunnel
disconnect() — disconnect NordVPN after run
rotate()     — switch to a random recent location (buttons 2-5) for a fresh IP
"""
import random
import subprocess
import time

CONNECT_WAIT = 12   # seconds to wait after connecting for tunnel to come up
ROTATE_WAIT  = 12   # same after rotating location


def _run(script: str, label: str, wait: int = 0) -> bool:
    """Run an AppleScript via osascript. Returns True on success."""
    try:
        result = subprocess.run(
            ["osascript"], input=script, text=True,
            timeout=60, capture_output=True,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            print(f"  [VPN] {label} failed: {err}")
            return False
        if wait:
            print(f"  [VPN] {label} — waiting {wait}s for tunnel")
            time.sleep(wait)
        else:
            print(f"  [VPN] {label}")
        return True
    except subprocess.TimeoutExpired:
        print(f"  [VPN] {label} timed out — continuing anyway")
        return False
    except Exception as e:
        print(f"  [VPN] {label} error: {e}")
        return False


def connect() -> bool:
    """Open NordVPN and connect using the most recent location."""
    print("  [VPN] connecting…")
    script = """\
tell application "System Events"
    do shell script "open -a NordVPN"
    delay 3
    set nordProc to application process "NordVPN"
    set sa to scroll area 1 of group 3 of group 1 of window 1 of nordProc
    -- Only click Connect if not already secured
    set isConnected to false
    try
        set isConnected to exists (static text "Secured by VPN" of group 1 of sa)
    end try
    if not isConnected then
        click button 1 of sa
    end if
end tell
"""
    return _run(script, "connect", wait=CONNECT_WAIT)


def disconnect() -> bool:
    """Disconnect NordVPN. Safe to call even if already disconnected."""
    print("  [VPN] disconnecting…")
    script = """\
tell application "System Events"
    if not (exists process "NordVPN") then return
    do shell script "open -a NordVPN"
    delay 3
    set nordProc to application process "NordVPN"
    set sa to scroll area 1 of group 3 of group 1 of window 1 of nordProc
    set isConnected to false
    try
        set isConnected to exists (static text "Secured by VPN" of group 1 of sa)
    end try
    if isConnected then
        click button 1 of sa
        delay 1
        click button 6 of group 1 of pop over 1 of sa
    end if
end tell
"""
    return _run(script, "disconnect")


def rotate() -> bool:
    """
    Switch to a different recent NordVPN location for a fresh IP.
    Picks randomly from buttons 2-5 (the last 4 used locations),
    skipping button 1 which is the current one.
    """
    btn = random.randint(2, 5)
    print(f"  [VPN] rotating to recent location #{btn}…")
    script = f"""\
tell application "System Events"
    do shell script "open -a NordVPN"
    delay 3
    set nordProc to application process "NordVPN"
    click button {btn} of UI element 1 of scroll area 1 of scroll area 1 of group 3 of group 1 of window 1 of nordProc
end tell
"""
    return _run(script, f"rotated to location #{btn}", wait=ROTATE_WAIT)
