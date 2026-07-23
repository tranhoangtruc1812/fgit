"""Utility helpers used across the fgit package."""
import logging
import os
import subprocess
import sys
from typing import List, Optional

DEFAULT_BRANCH = "dev"
MANIFEST_DIR = ".fgit"
MANIFEST_FILE = "manifest.json"

# True if stdout is a terminal (used for ANSI colors).
_IS_TTY = sys.stdout.isatty()


class Colors:
    """Minimal ANSI color helpers."""

    RESET = "\033[0m" if _IS_TTY else ""
    BOLD = "\033[1m" if _IS_TTY else ""
    RED = "\033[31m" if _IS_TTY else ""
    GREEN = "\033[32m" if _IS_TTY else ""
    YELLOW = "\033[33m" if _IS_TTY else ""
    BLUE = "\033[34m" if _IS_TTY else ""
    CYAN = "\033[36m" if _IS_TTY else ""


def setup_logging() -> None:
    """Configure fgit logging to stderr."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )


def log(message: str, level: str = "info") -> None:
    """Print a colored log message to stderr."""
    if level == "error":
        prefix = f"{Colors.RED}✖{Colors.RESET}"
    elif level == "warning":
        prefix = f"{Colors.YELLOW}⚠{Colors.RESET}"
    elif level == "success":
        prefix = f"{Colors.GREEN}✔{Colors.RESET}"
    elif level == "info":
        prefix = f"{Colors.BLUE}→{Colors.RESET}"
    else:
        prefix = ""
    sys.stderr.write(f"{prefix} {message}\n")


def run_command(
    cmd: List[str],
    cwd: Optional[str] = None,
    check: bool = True,
    capture: bool = False,
    env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """Run a shell command and return the completed process."""
    logging.debug("Running: %s (cwd=%s)", " ".join(cmd), cwd)
    kwargs = {
        "cwd": cwd,
        "text": True,
        "stdout": subprocess.PIPE if capture else None,
        "stderr": subprocess.PIPE if capture else None,
    }
    if env is not None:
        kwargs["env"] = env
    if check:
        return subprocess.run(cmd, check=True, **kwargs)
    return subprocess.run(cmd, check=False, **kwargs)


def run_command_capture(cmd: List[str], cwd: Optional[str] = None, env: Optional[dict] = None) -> str:
    """Run a command and capture stdout as a stripped string."""
    try:
        result = run_command(cmd, cwd=cwd, check=True, capture=True, env=env)
        return result.stdout.strip()
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        if stderr:
            raise RuntimeError(stderr) from exc
        raise


def find_root(start_dir: Optional[str] = None, explicit_root: Optional[str] = None) -> str:
    """Resolve the fgit root directory.

    Resolution order:
      1. explicit_root argument (e.g. --root)
      2. FGIT_ROOT environment variable
      3. walk up from start_dir / cwd looking for a .fgit directory
      4. active_project or default_root from ~/.config/fgit/config.json

    Raises FileNotFoundError if no root can be found.
    """
    if explicit_root:
        return os.path.abspath(explicit_root)

    env_root = os.environ.get("FGIT_ROOT")
    if env_root:
        return os.path.abspath(env_root)

    try:
        return _find_root_by_walk(start_dir)
    except FileNotFoundError:
        from fgit.config import load_config

        cfg = load_config()
        configured_root = cfg.get_active_root()
        if configured_root:
            return os.path.abspath(configured_root)
        raise


def _find_root_by_walk(start_dir: Optional[str] = None) -> str:
    """Walk up from start_dir until a .fgit directory is found."""
    current = os.path.abspath(start_dir or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(current, MANIFEST_DIR)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    raise FileNotFoundError(
        "Cannot find an fgit root. Make sure you are inside a repo that contains "
        "a `.fgit` directory, or set a default root via `fgit project use`, "
        "`fgit config root`, or FGIT_ROOT."
    )


def sibling_dir(root_dir: str, name: str) -> str:
    """Return the absolute path of a sibling repo next to root."""
    return os.path.abspath(os.path.join(root_dir, "..", name))


def print_clone_tree(root_dir: str, repo_names: List[str]) -> None:
    """Print a visual tree of the cloned root and its sibling repos."""
    root_name = os.path.basename(root_dir) or root_dir
    print(f"{root_name}/")
    print("├── .fgit/")
    print("│   └── manifest.json")
    if not repo_names:
        print("└── (no child repositories)")
        return
    count = len(repo_names)
    for i, name in enumerate(repo_names):
        connector = "└──" if i == count - 1 else "├──"
        print(f"{connector} ../{name}/")
    parent = os.path.dirname(root_dir)
    print(f"\nLayout: child repositories are siblings of {root_name} under {parent}")
