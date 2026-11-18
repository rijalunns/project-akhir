"""Safety guardrails untuk skrip remediasi buatan AI.

Pendekatan: denylist berbasis regex setelah normalisasi skrip.
Ini lapisan pengaman tambahan, BUKAN pengganti persetujuan admin.
"""
import re

MAX_SCRIPT_LENGTH = 2000

# (pola, alasan) — dicocokkan case-insensitive terhadap skrip yang sudah dinormalisasi
BLOCKED_PATTERNS: list[tuple[str, str]] = [
    # Hapus massal: rm rekursif ke root / home / direktori sistem level atas (mis. /, /*, /etc, /var/*)
    (r"\brm\s+(-[a-z-]*\s+)*(-[a-z]*r[a-z]*|--recursive)\s+(-[a-z-]*\s+)*(\S+\s+)*"
     r"(/\*?|~/?\*?|\$home/?\*?|/(etc|bin|sbin|usr|lib|lib64|boot|var|root|home|opt|srv|dev|proc|sys)/?\*?)(?=\s|$|[;&|])",
     "rm rekursif ke direktori sistem/root"),
    (r"--no-preserve-root", "rm --no-preserve-root"),
    # Filesystem / partisi / disk
    (r"\bmkfs(\.\w+)?\b", "format filesystem (mkfs)"),
    (r"\b(fdisk|sfdisk|cfdisk|gdisk|sgdisk|parted|wipefs|blkdiscard)\b", "manipulasi partisi/disk"),
    (r"\bdd\b[^\n]*\bof=/dev/", "dd menulis langsung ke device"),
    (r"\bshred\b", "shred (hapus permanen)"),
    (r">\s*/dev/(sd|vd|hd|xvd|nvme|mmcblk|dm-|mapper/)", "redirect menimpa block device"),
    (r"\b(pvremove|vgremove|lvremove|zpool\s+destroy|zfs\s+destroy)\b", "hapus volume LVM/ZFS"),
    # Fork bomb
    (r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:", "fork bomb"),
    # Mematikan / restart host
    (r"\b(shutdown|poweroff|halt|reboot)\b", "shutdown/reboot host"),
    (r"\binit\s+[06]\b|\btelinit\s+[06]\b", "init 0/6"),
    (r"\bsystemctl\s+(poweroff|reboot|halt|kexec|emergency|rescue)\b", "systemctl power action"),
    (r"\becho\s+[a-z]\s*>\s*/proc/sysrq-trigger", "sysrq trigger"),
    # Membunuh semua proses / init
    (r"\bkill\s+(-\S+\s+)*-1\b", "kill semua proses (kill -1)"),
    (r"\bkill\s+(-\S+\s+)*1\b(?!\d)", "kill PID 1 (init/systemd)"),
    (r"\bkillall5\b", "killall5"),
    # Permission / akun
    (r"\bchmod\s+(-[a-z]+\s+)*[0-7]*777\s+/(\s|$)", "chmod 777 root"),
    (r"\bchown\s+(-[a-z]+\s+)*\S+\s+/(\s|$)", "chown root filesystem"),
    (r"\b(passwd|userdel|usermod|chpasswd)\b", "ubah akun/password"),
    (r">\s*/etc/(passwd|shadow|sudoers|fstab|hosts)\b", "menimpa file sistem penting"),
    # Download & eksekusi kode dari luar / obfuscation
    (r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", "download lalu dieksekusi (curl|sh)"),
    (r"\bbase64\s+(-d|--decode)\b[^\n]*\|\s*(ba|z|da)?sh\b", "eksekusi payload base64"),
    (r"\b(nc|ncat|netcat)\b[^\n]*\s-e\s", "reverse shell netcat"),
    (r"/dev/tcp/", "reverse shell /dev/tcp"),
    # Firewall / jaringan dimatikan
    (r"\biptables\s+(-F|--flush)\b", "flush firewall"),
    (r"\b(ip\s+link\s+set\s+\S+\s+down|ifdown)\b", "mematikan interface jaringan"),
    # Mematikan layanan inti
    (r"\bsystemctl\s+(stop|disable|mask)\s+(sshd?|networking|network|systemd-\S+|qemu-guest-agent)\b",
     "menghentikan layanan inti"),
    # Crontab dihapus
    (r"\bcrontab\s+-r\b", "hapus crontab"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), reason) for p, reason in BLOCKED_PATTERNS]


def _normalize(script: str) -> str:
    """Rapikan skrip supaya trik penulisan tidak lolos regex."""
    s = script.replace("\\\n", " ")              # line continuation
    s = re.sub(r"[\"'`]", "", s)                   # hapus quote: r'm' -> rm
    s = re.sub(r"\$\{?IFS\}?", " ", s)            # ${IFS} sebagai spasi
    s = re.sub(r"\\(?=[a-z])", "", s, flags=re.I)  # \rm -> rm
    s = re.sub(r"(^|[;&|\s(])(sudo|command|exec|nohup|env|time|nice(\s+-n\s*-?\d+)?)\s+", r"\1", s)
    s = re.sub(r"(?<![\w/])/(usr/)?(local/)?s?bin/", "", s)  # /bin/rm -> rm
    s = re.sub(r"[ \t]+", " ", s)
    return s


def sanitize_remediation_script(script: str | None) -> tuple[bool, str]:
    if not isinstance(script, str) or not script.strip():
        return False, "Script kosong"

    if len(script) > MAX_SCRIPT_LENGTH:
        return False, f"Script terlalu panjang (> {MAX_SCRIPT_LENGTH} karakter)"

    if "\x00" in script:
        return False, "Script mengandung karakter null"

    normalized = _normalize(script)
    for candidate in (script, normalized):
        for pattern, reason in _COMPILED:
            if pattern.search(candidate):
                return False, f"Perintah diblokir oleh Safety Guardrails ({reason})"

    return True, "Valid"
