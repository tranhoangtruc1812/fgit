"""SSH key generation and configuration helpers for fgit."""
import os
import subprocess
from typing import Optional

SSH_DIR = os.path.expanduser("~/.ssh")
SSH_CONFIG_FILE = os.path.join(SSH_DIR, "config")


def key_path(host: str, key_type: str = "ed25519") -> str:
    """Return the default private key path for a given host."""
    slug = host.replace(".", "-").replace(":", "-")
    return os.path.join(SSH_DIR, f"id_{key_type}_fgit_{slug}")


def generate_key(host: str, key_type: str = "ed25519", comment: Optional[str] = None, force: bool = False) -> str:
    """Generate a new SSH key pair for the given host. Returns the private key path."""
    os.makedirs(SSH_DIR, exist_ok=True)
    os.chmod(SSH_DIR, 0o700)

    priv_path = key_path(host, key_type)
    pub_path = priv_path + ".pub"

    if os.path.isfile(priv_path) and not force:
        raise FileExistsError(f"Key already exists at {priv_path}. Use --force to overwrite.")

    if force:
        for existing in (priv_path, pub_path):
            if os.path.isfile(existing):
                os.remove(existing)

    subprocess.run(
        [
            "ssh-keygen",
            "-t", key_type,
            "-f", priv_path,
            "-N", "",
            "-C", comment or f"fgit@{host}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    os.chmod(priv_path, 0o600)
    return priv_path


def read_public_key(priv_path: str) -> str:
    """Read the public key content for a given private key path."""
    pub_path = priv_path + ".pub"
    with open(pub_path, "r", encoding="utf-8") as fh:
        return fh.read().strip()


def add_ssh_config_entry(host: str, priv_path: str) -> bool:
    """Add a Host entry to ~/.ssh/config pointing at the generated key.

    Returns True if a new entry was written, False if one already exists for this host.
    """
    os.makedirs(SSH_DIR, exist_ok=True)
    existing = ""
    if os.path.isfile(SSH_CONFIG_FILE):
        with open(SSH_CONFIG_FILE, "r", encoding="utf-8") as fh:
            existing = fh.read()

    if f"Host {host}" in existing:
        return False

    entry = (
        f"\nHost {host}\n"
        f"    HostName {host}\n"
        f"    IdentityFile {priv_path}\n"
        f"    IdentitiesOnly yes\n"
    )
    with open(SSH_CONFIG_FILE, "a", encoding="utf-8") as fh:
        fh.write(entry)
    os.chmod(SSH_CONFIG_FILE, 0o600)
    return True
