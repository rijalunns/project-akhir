import os
import re

BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"mkfs",
    r"fdisk",
    r"parted",
    r"dd\s+if=.*of=/dev/sd",
    r":\(\)\s*\{\s*:\|\:&\s*\};:",
    r"shutdown",
    r"poweroff",
    r"reboot",
    r"init\s+0",
]

def sanitize_remediation_script(script: str) -> tuple[bool, str]:
    if not script or not script.strip():
        return False, "Script kosong"

    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, script, re.IGNORECASE):
            return False, f"Perintah diblokir oleh Safety Guardrails (terdeteksi pola berbahaya: {pattern})"

    return True, "Valid"
