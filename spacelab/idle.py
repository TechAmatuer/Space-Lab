"""How long since anyone touched this Mac's keyboard, mouse or trackpad (macOS). None on other systems."""
import re
import subprocess
import sys


def user_idle_seconds():
    if sys.platform != 'darwin':
        return None
    try:
        out = subprocess.run(['ioreg', '-c', 'IOHIDSystem'], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
        return int(m.group(1)) / 1e9 if m else None
    except Exception:
        return None
