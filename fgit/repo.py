"""Per-repo Git operations used by fgit."""
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from fgit import credentials as creds
from fgit.utils import Colors, run_command, run_command_capture


@dataclass
class RepoStatus:
    """Status summary for a single repository."""

    name: str
    path: str
    branch: str
    exists: bool
    is_git: bool
    dirty: bool = False
    ahead: int = 0
    behind: int = 0
    error: Optional[str] = None

    def format(self) -> str:
        if self.error:
            return f"{Colors.RED}{self.name:30}{Colors.RESET} {self.error}"
        if not self.exists:
            return f"{Colors.YELLOW}{self.name:30}{Colors.RESET} not cloned"
        if not self.is_git:
            return f"{Colors.RED}{self.name:30}{Colors.RESET} not a git repo"

        status_parts = []
        if self.dirty:
            status_parts.append(f"{Colors.YELLOW}dirty{Colors.RESET}")
        if self.ahead:
            status_parts.append(f"{Colors.GREEN}+{self.ahead}{Colors.RESET}")
        if self.behind:
            status_parts.append(f"{Colors.RED}-{self.behind}{Colors.RESET}")
        status = ",".join(status_parts) if status_parts else "clean"
        return f"{Colors.CYAN}{self.name:30}{Colors.RESET} {self.branch:20} {status}"


class Repo:
    """Wrapper around a single Git repository."""

    def __init__(self, name: str, url: str, path: str, default_branch: str = "dev"):
        self.name = name
        self.url = url
        self.path = path
        self.default_branch = default_branch

    def _git_env(self, use_credentials: bool = True) -> Tuple[dict, Optional[str]]:
        """Return Git environment with credentials injected via GIT_ASKPASS."""
        if not use_credentials:
            return os.environ.copy(), None
        username, password = creds.get_credentials()
        return creds.git_env_with_credentials(username, password)

    def _git_run(
        self,
        cmd: List[str],
        cwd: Optional[str] = None,
        check: bool = True,
        capture: bool = False,
        use_credentials: bool = True,
    ) -> subprocess.CompletedProcess:
        env, askpass = self._git_env(use_credentials=use_credentials)
        try:
            return run_command(cmd, cwd=cwd, check=check, capture=capture, env=env)
        finally:
            creds.clear_askpass(askpass)

    def _git_capture(self, cmd: List[str], cwd: Optional[str] = None, use_credentials: bool = True) -> str:
        env, askpass = self._git_env(use_credentials=use_credentials)
        try:
            return run_command_capture(cmd, cwd=cwd, env=env)
        finally:
            creds.clear_askpass(askpass)

    def exists(self) -> bool:
        return os.path.isdir(self.path)

    def is_git_repo(self) -> bool:
        return os.path.isdir(os.path.join(self.path, ".git"))

    def clone(self) -> None:
        """Clone the repository into its sibling path."""
        parent = os.path.dirname(self.path)
        os.makedirs(parent, exist_ok=True)
        url = _to_https_url(self.url)
        self._git_run(["git", "clone", url, self.path])

    def remote_url(self) -> str:
        """Return the origin remote URL."""
        self._ensure_repo()
        return self._git_capture(
            ["git", "remote", "get-url", "origin"], cwd=self.path, use_credentials=False
        )

    def pull(self, dry_run: bool = False) -> None:
        self._ensure_repo()
        branch = self.current_branch()
        if dry_run:
            return
        self._git_run(["git", "pull", "--ff-only", "origin", branch], cwd=self.path)

    def push(self, dry_run: bool = False) -> None:
        self._ensure_repo()
        branch = self.current_branch()
        if dry_run:
            return
        self._git_run(["git", "push", "origin", branch], cwd=self.path)

    def sync(self, dry_run: bool = False) -> None:
        self._ensure_repo()
        self.pull(dry_run=dry_run)
        self.push(dry_run=dry_run)

    def current_branch(self) -> str:
        self._ensure_repo()
        return self._git_capture(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.path, use_credentials=False)

    def check_health(self) -> Dict[str, Any]:
        """Return a health report for this repo.

        Result shape: {"name": str, "ok": bool, "issues": [str]}
        """
        issues: List[str] = []
        if not self.exists():
            issues.append("not cloned")
        elif not self.is_git_repo():
            issues.append("not a git repo")
        else:
            try:
                actual_url = self.remote_url()
                expected_https = _to_https_url(self.url)
                if actual_url != self.url and actual_url != expected_https:
                    issues.append(f"remote url mismatch: {actual_url}")
            except (subprocess.CalledProcessError, RuntimeError) as exc:
                issues.append(f"cannot read remote: {exc}")

            try:
                branch = self.current_branch()
                if branch != self.default_branch:
                    issues.append(f"on branch '{branch}', expected '{self.default_branch}'")
            except (subprocess.CalledProcessError, RuntimeError) as exc:
                issues.append(f"cannot read branch: {exc}")

            if self._is_dirty():
                issues.append("dirty working tree")

        return {"name": self.name, "ok": not issues, "issues": issues}

    def status(self) -> RepoStatus:
        if not self.exists():
            return RepoStatus(self.name, self.path, "", exists=False, is_git=False)
        if not self.is_git_repo():
            return RepoStatus(self.name, self.path, "", exists=True, is_git=False)

        try:
            branch = self.current_branch()
            dirty = self._is_dirty()
            ahead, behind = self._ahead_behind(branch)
            return RepoStatus(
                name=self.name,
                path=self.path,
                branch=branch,
                exists=True,
                is_git=True,
                dirty=dirty,
                ahead=ahead,
                behind=behind,
            )
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            return RepoStatus(
                name=self.name,
                path=self.path,
                branch="",
                exists=True,
                is_git=True,
                error=str(exc),
            )

    def has_local_branch(self, branch: str) -> bool:
        """True if ``branch`` exists as a local branch."""
        return self._ref_exists(f"refs/heads/{branch}")

    def has_remote_branch(self, branch: str) -> bool:
        """True if ``origin/branch`` exists as a remote-tracking ref."""
        return self._ref_exists(f"refs/remotes/origin/{branch}")

    def fetch_branch(self, branch: str) -> bool:
        """Fetch a single branch from origin into its remote-tracking ref.

        Returns True if the branch was fetched, False if origin has no such
        branch. Raises RuntimeError for any other failure (auth, network, ...)
        so a connectivity problem is never reported as a missing branch.
        """
        self._ensure_full_refspec()

        # Use stored credentials when there are any, but never prompt: checkout
        # must stay non-interactive, and SSH remotes need no credentials at all.
        stored = creds.find_credentials()
        if stored:
            env, askpass = creds.git_env_with_credentials(*stored)
        else:
            env, askpass = os.environ.copy(), None
        try:
            result = run_command(
                ["git", "fetch", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}"],
                cwd=self.path,
                check=False,
                capture=True,
                env=env,
            )
        finally:
            creds.clear_askpass(askpass)

        if result.returncode == 0:
            return True
        stderr = (result.stderr or "").strip()
        if "couldn't find remote ref" in stderr:
            return False
        raise RuntimeError(stderr or f"git fetch origin {branch} failed")

    def _ensure_full_refspec(self) -> None:
        """Make sure origin's fetch refspec covers every branch.

        Clones made with ``--single-branch`` map only one branch into
        refs/remotes/origin/, so Git refuses to set up tracking for any other
        branch. fgit switches branches across repos, so widen the refspec to
        the standard wildcard the way a plain ``git clone`` would.
        """
        wildcard = "refs/heads/*:refs/remotes/origin/*"
        try:
            refspecs = self._git_capture(
                ["git", "config", "--get-all", "remote.origin.fetch"],
                cwd=self.path,
                use_credentials=False,
            ).splitlines()
        except (RuntimeError, subprocess.CalledProcessError):
            return

        if any(spec.strip().lstrip("+") == wildcard for spec in refspecs):
            return
        self._git_capture(
            ["git", "config", "--add", "remote.origin.fetch", f"+{wildcard}"],
            cwd=self.path,
            use_credentials=False,
        )

    def create_branch(self, name: str, dry_run: bool = False) -> None:
        self._ensure_repo()
        # Nothing to do if the branch already exists locally.
        if self.has_local_branch(name):
            return
        if dry_run:
            return
        # Create from default_branch, fetching it first if it is not known yet.
        base = self.default_branch
        if not self.has_remote_branch(base):
            self.fetch_branch(base)
        if not self.has_remote_branch(base):
            raise RuntimeError(
                f"cannot create '{name}': base branch 'origin/{base}' does not exist on origin"
            )
        self._git_capture(
            ["git", "checkout", "-b", name, f"origin/{base}"],
            cwd=self.path,
            use_credentials=False,
        )

    def delete_branch(self, name: str, dry_run: bool = False) -> None:
        self._ensure_repo()
        if dry_run:
            return
        self._git_run(["git", "branch", "-D", name], cwd=self.path, use_credentials=False)

    def checkout(self, branch: str, dry_run: bool = False) -> None:
        """Check out ``branch``, creating a tracking branch from origin if needed."""
        self._ensure_repo()
        if dry_run:
            return

        # The branch is already here: just switch to it.
        if self.has_local_branch(branch):
            self._git_capture(["git", "checkout", branch], cwd=self.path, use_credentials=False)
            return

        # It may exist on origin but never have been fetched by this clone.
        if not self.has_remote_branch(branch):
            self.fetch_branch(branch)

        if not self.has_remote_branch(branch):
            raise RuntimeError(
                f"branch '{branch}' does not exist locally or on origin "
                f"(create it with `fgit branch create {branch}`)"
            )

        self._checkout_tracking(branch)

    def _checkout_tracking(self, branch: str) -> None:
        """Create a local ``branch`` from ``origin/branch`` and track it."""
        try:
            self._git_capture(
                ["git", "checkout", "-b", branch, "--track", f"origin/{branch}"],
                cwd=self.path,
                use_credentials=False,
            )
            return
        except (RuntimeError, subprocess.CalledProcessError):
            pass

        # Tracking could not be set up (unusual remote layout). The branch
        # itself is still worth having, so create it without an upstream.
        self._git_capture(
            ["git", "checkout", "-b", branch, f"origin/{branch}"],
            cwd=self.path,
            use_credentials=False,
        )

    def list_branches(self) -> str:
        self._ensure_repo()
        return self._git_capture(["git", "branch", "-vv"], cwd=self.path, use_credentials=False)

    def sync_from(self, branch: str, dry_run: bool = False) -> None:
        """Merge the latest state of ``branch`` into the current branch.

        Steps:
          1. Remember the current branch.
          2. Fetch origin.
          3. Checkout and pull the target branch.
          4. Checkout the original branch.
          5. Merge the target branch.

        If a merge conflict occurs, the merge is aborted and an error is raised.
        """
        self._ensure_repo()
        current_branch = self.current_branch()

        if dry_run:
            return

        # 1. Fetch latest refs from origin.
        self._git_run(["git", "fetch", "origin"], cwd=self.path)

        # 2. Checkout the target branch (create a tracking branch if needed).
        self.checkout(branch)

        # 3. Pull latest code for the target branch.
        self._git_run(["git", "pull", "origin", branch], cwd=self.path)

        # 4. Checkout back to the original branch.
        self._git_run(["git", "checkout", current_branch], cwd=self.path, use_credentials=False)

        # 5. Merge the target branch into the current branch.
        try:
            self._git_run(["git", "merge", branch], cwd=self.path, use_credentials=False)
        except subprocess.CalledProcessError:
            # Abort the merge to leave the working tree clean.
            self._git_run(["git", "merge", "--abort"], cwd=self.path, use_credentials=False)
            raise RuntimeError(
                f"merge conflict when merging {branch} into {current_branch}"
            )

    def use_https_remote(self) -> None:
        """Convert the origin remote URL to HTTPS if it is currently SSH."""
        self._ensure_repo()
        url = self._git_capture(["git", "remote", "get-url", "origin"], cwd=self.path, use_credentials=False)
        if url.startswith("https://"):
            return
        if url.startswith("git@"):
            # git@host:group/repo.git -> https://host/group/repo.git
            new_url = url.replace(":", "/", 1).replace("git@", "https://")
            if not new_url.endswith(".git"):
                new_url += ".git"
            self._git_run(["git", "remote", "set-url", "origin", new_url], cwd=self.path, use_credentials=False)
            return

    def clean(self, dry_run: bool = True, ignored: bool = False) -> str:
        """Remove untracked files from the repository.

        By default runs in dry-run mode so the user can review what would be deleted.
        """
        self._ensure_repo()
        cmd = ["git", "clean", "-d"]
        if dry_run:
            cmd.append("-n")
        else:
            cmd.append("-f")
        if ignored:
            cmd.append("-x")
        return self._git_capture(cmd, cwd=self.path, use_credentials=False)

    def _ensure_repo(self) -> None:
        if not self.exists():
            raise FileNotFoundError(f"Repo {self.name} is not cloned at {self.path}")
        if not self.is_git_repo():
            raise RuntimeError(f"{self.path} is not a Git repository")

    def _ref_exists(self, ref: str) -> bool:
        try:
            self._git_run(
                ["git", "show-ref", "--verify", "--quiet", ref],
                cwd=self.path,
                capture=True,
                use_credentials=False,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def _is_dirty(self) -> bool:
        try:
            output = self._git_capture(["git", "status", "--porcelain"], cwd=self.path, use_credentials=False)
            return bool(output)
        except subprocess.CalledProcessError:
            return False

    def _ahead_behind(self, branch: str) -> Tuple[int, int]:
        try:
            output = self._git_capture(
                ["git", "rev-list", "--left-right", "--count", f"origin/{branch}...{branch}"],
                cwd=self.path,
                use_credentials=False,
            )
            parts = output.split()
            if len(parts) == 2:
                return int(parts[1]), int(parts[0])
        except (subprocess.CalledProcessError, RuntimeError, ValueError):
            pass
        return 0, 0


def _to_https_url(url: str) -> str:
    """Convert an SSH Git URL to HTTPS when possible."""
    url = url.strip()
    if url.startswith("https://"):
        return url
    if url.startswith("git@"):
        # git@host:group/repo.git -> https://host/group/repo.git
        rest = url[len("git@"):]
        if ":" in rest:
            host, path = rest.split(":", 1)
            return f"https://{host}/{path}"
    return url
