"""Credential management for fgit HTTPS authentication."""
import getpass
import os
import subprocess
import sys
import tempfile
from typing import Optional, Tuple

from fgit.utils import log


CONFIG_DIR = os.path.expanduser("~/.config/fgit")
NETRC_FILE = os.path.join(CONFIG_DIR, "netrc")
NETRC_GPG_FILE = os.path.join(CONFIG_DIR, "netrc.gpg")


def _netrc_lines() -> list:
    """Return parsed netrc entries as list of (machine, login, password)."""
    content = _read_netrc()
    if not content:
        return []
    entries = []
    i = 0
    tokens = content.split()
    while i < len(tokens):
        if tokens[i] == "machine":
            machine = tokens[i + 1] if i + 1 < len(tokens) else ""
            login = ""
            password = ""
            i += 2
            while i < len(tokens) and tokens[i] != "machine":
                if tokens[i] == "login" and i + 1 < len(tokens):
                    login = tokens[i + 1]
                    i += 2
                elif tokens[i] == "password" and i + 1 < len(tokens):
                    password = tokens[i + 1]
                    i += 2
                else:
                    i += 1
            entries.append((machine, login, password))
        else:
            i += 1
    return entries


def _read_netrc() -> str:
    """Read netrc content, decrypting .gpg if available."""
    if os.path.isfile(NETRC_GPG_FILE):
        try:
            result = subprocess.run(
                ["gpg", "--decrypt", NETRC_GPG_FILE],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            log(f"Could not decrypt {NETRC_GPG_FILE}: {exc}", level="warning")
            return ""
    if os.path.isfile(NETRC_FILE):
        with open(NETRC_FILE, "r", encoding="utf-8") as fh:
            return fh.read()
    return ""


def find_credentials(machine: str = "gitlab.atomsolution.vn") -> Optional[Tuple[str, str]]:
    """Return stored (username, password) for a machine, or None.

    Unlike :func:`get_credentials` this never prompts, so it is safe to call
    from background threads and from commands that may not need auth at all.
    """
    for entry_machine, login, password in _netrc_lines():
        if entry_machine == machine:
            return login, password
    return None


def get_credentials(machine: str = "gitlab.atomsolution.vn") -> Tuple[str, str]:
    """Return (username, password) for a machine.

    Prompts interactively if not found in netrc.
    """
    for entry_machine, login, password in _netrc_lines():
        if entry_machine == machine:
            return login, password

    log(f"No credentials found for {machine} in {CONFIG_DIR}/netrc", level="warning")
    if not sys.stdin.isatty():
        raise RuntimeError(
            f"No credentials found for {machine}. Run: fgit credential set --machine {machine}"
        )
    username = input("GitLab username: ")
    password = getpass.getpass("GitLab password: ")
    save_credentials(machine, username, password)
    return username, password


def save_credentials(machine: str, username: str, password: str) -> None:
    """Save credentials to ~/.config/fgit/netrc."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    entries = _netrc_lines()
    # Remove existing entry for this machine.
    entries = [(m, l, p) for m, l, p in entries if m != machine]
    entries.append((machine, username, password))

    lines = []
    for m, l, p in entries:
        lines.append(f"machine {m}")
        lines.append(f"login {l}")
        lines.append(f"password {p}")
        lines.append("")

    with open(NETRC_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    os.chmod(NETRC_FILE, 0o600)
    log(f"Credentials saved to {NETRC_FILE}", level="success")


def encrypt_netrc() -> None:
    """Encrypt ~/.config/fgit/netrc to ~/.config/fgit/netrc.gpg using GPG."""
    if not os.path.isfile(NETRC_FILE):
        log(f"No {NETRC_FILE} to encrypt", level="error")
        return
    try:
        subprocess.run(
            ["gpg", "--symmetric", "--cipher-algo", "AES256", "--output", NETRC_GPG_FILE, NETRC_FILE],
            check=True,
        )
        os.remove(NETRC_FILE)
        os.chmod(NETRC_GPG_FILE, 0o600)
        log(f"Encrypted credentials saved to {NETRC_GPG_FILE}", level="success")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log(f"Failed to encrypt netrc: {exc}", level="error")


def decrypt_netrc() -> None:
    """Decrypt ~/.config/fgit/netrc.gpg back to ~/.config/fgit/netrc."""
    if not os.path.isfile(NETRC_GPG_FILE):
        log(f"No {NETRC_GPG_FILE} to decrypt", level="error")
        return
    try:
        result = subprocess.run(
            ["gpg", "--decrypt", "--output", NETRC_FILE, NETRC_GPG_FILE],
            check=True,
        )
        os.chmod(NETRC_FILE, 0o600)
        log(f"Decrypted credentials saved to {NETRC_FILE}", level="success")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log(f"Failed to decrypt netrc: {exc}", level="error")


def _askpass_script(username: str, password: str) -> str:
    """Create a temporary GIT_ASKPASS script that returns credentials by prompt."""
    script = f"""#!/usr/bin/env python3
import sys
prompt = sys.argv[1] if len(sys.argv) > 1 else ""
if "Username" in prompt or "username" in prompt or "user" in prompt.lower():
    print({username!r})
elif "Password" in prompt or "password" in prompt or "pass" in prompt.lower():
    print({password!r})
else:
    print("")
"""
    fd, path = tempfile.mkstemp(suffix="-fgit-askpass")
    with os.fdopen(fd, "w") as fh:
        fh.write(script)
    os.chmod(path, 0o700)
    return path


def git_env_with_credentials(username: str, password: str) -> dict:
    """Return a copy of the environment with GIT_ASKPASS configured."""
    env = os.environ.copy()
    askpass = _askpass_script(username, password)
    env["GIT_ASKPASS"] = askpass
    env["SSH_ASKPASS_REQUIRE"] = "never"
    return env, askpass


def clear_askpass(path: Optional[str]) -> None:
    """Remove the temporary askpass script."""
    if path and os.path.isfile(path):
        os.remove(path)
