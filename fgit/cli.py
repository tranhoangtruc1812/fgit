"""Command-line interface for fgit."""
import argparse
import getpass
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List

from fgit import __version__, credentials as creds, ssh
from fgit.config import Config, load_config
from fgit.manifest import Manifest, RepoEntry, default_manifest
from fgit.repo import Repo
from fgit.utils import Colors, find_root, log, print_clone_tree, run_command


DEFAULT_JOBS = 4


def _repos_from_manifest(manifest: Manifest, root_dir: str) -> List[Repo]:
    return [
        Repo(
            name=entry.name,
            url=entry.url,
            path=manifest.repo_path(entry, root_dir),
            default_branch=manifest.default_branch,
        )
        for entry in manifest.repos
    ]


def _run_parallel(
    repos: List[Repo],
    fn: Callable[[Repo], None],
    jobs: int = DEFAULT_JOBS,
    silent: bool = False,
    description: str = "operation",
) -> None:
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        future_to_repo = {executor.submit(fn, repo): repo for repo in repos}
        for future in as_completed(future_to_repo):
            repo = future_to_repo[future]
            try:
                future.result()
                if not silent:
                    log(f"{repo.name}: {description} completed", level="success")
            except Exception as exc:  # noqa: BLE001
                log(f"{repo.name}: {description} failed: {exc}", level="error")


def cmd_clone(args: argparse.Namespace) -> int:
    """Clone the root repository and then clone all child repositories."""
    url = args.url
    dest = args.dest or os.path.basename(url).replace(".git", "")
    dest = os.path.abspath(dest)

    if os.path.exists(dest):
        log(f"Destination already exists: {dest}", level="error")
        return 1

    log(f"Cloning root repository from {url} into {dest}")
    run_command(["git", "clone", url, dest])

    root_dir = dest
    manifest_path = os.path.join(root_dir, ".fgit", "manifest.json")
    if not os.path.isfile(manifest_path):
        log(f"No manifest found in root. Run `fgit init` inside {root_dir}.", level="warning")
        return 0

    manifest = Manifest.load(manifest_path)
    repos = _repos_from_manifest(manifest, root_dir)
    log(f"Cloning {len(repos)} child repositories...")
    _run_parallel(repos, lambda repo: repo.clone() if not repo.exists() else None, jobs=args.jobs, description="clone")
    repo_names = [repo.name for repo in repos]
    print_clone_tree(root_dir, repo_names)
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Clone child repositories from an existing root's manifest."""
    root_dir = find_root(args.root)
    manifest = Manifest.load()
    repos = _repos_from_manifest(manifest, root_dir)
    missing = [repo for repo in repos if not repo.exists()]
    if not missing:
        log("All repositories are already cloned.", level="success")
        return 0
    log(f"Cloning {len(missing)} child repositories...")
    _run_parallel(missing, lambda repo: repo.clone(), jobs=args.jobs, description="clone")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Create an fgit manifest from sibling repositories around the root."""
    root_dir = find_root(args.root)
    manifest_path = os.path.join(root_dir, ".fgit", "manifest.json")
    manifest = default_manifest()

    existing_names: List[str] = []
    if os.path.isfile(manifest_path):
        try:
            existing = Manifest.load(manifest_path)
            existing_names = [r.name for r in existing.repos]
            manifest = existing
            log(f"Loaded existing manifest with {len(existing.repos)} repos.")
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            log(f"Could not load existing manifest: {exc}. Creating a new one.", level="warning")

    discovered: List[RepoEntry] = []
    if args.from_siblings:
        parent_dir = os.path.dirname(root_dir)
        for entry in os.listdir(parent_dir):
            sibling_path = os.path.join(parent_dir, entry)
            git_dir = os.path.join(sibling_path, ".git")
            if os.path.isdir(git_dir) and sibling_path != root_dir:
                try:
                    url = subprocess.check_output(
                        ["git", "remote", "get-url", "origin"],
                        cwd=sibling_path,
                        text=True,
                    ).strip()
                except subprocess.CalledProcessError:
                    url = ""
                branch = args.default_branch
                try:
                    branch = subprocess.check_output(
                        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                        cwd=sibling_path,
                        text=True,
                    ).strip()
                except subprocess.CalledProcessError:
                    pass
                discovered.append(RepoEntry(name=entry, url=url, branch=branch))

    existing_set = set(existing_names)
    missing = [r for r in discovered if r.name not in existing_set]
    stale = [name for name in existing_names if name not in {r.name for r in discovered}]

    if missing:
        if args.add_missing:
            manifest.repos.extend(missing)
            log(f"Added {len(missing)} new sibling repos to manifest:")
            for repo in missing:
                log(f"  + {repo.name}: {repo.url}")
        else:
            log(f"Found {len(missing)} sibling repos not in manifest. Use --add-missing to include them:", level="warning")
            for repo in missing:
                log(f"  ? {repo.name}: {repo.url}")

    if stale:
        log(f"Warning: {len(stale)} repos in manifest are missing on disk:", level="warning")
        for name in stale:
            log(f"  - {name}")

    if not existing_names and not missing:
        log("No sibling repositories found. You can add repos manually to .fgit/manifest.json")

    manifest.repos.sort(key=lambda r: r.name)
    manifest.save()
    log(f"Manifest saved to {manifest_path}", level="success")
    for repo in manifest.repos:
        log(f"  - {repo.name}: {repo.url}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root_dir = find_root(args.root)
    manifest = Manifest.load()
    repos = _repos_from_manifest(manifest, root_dir)
    log(f"{Colors.BOLD}{'NAME':30} {'BRANCH':20} STATUS{Colors.RESET}")
    for repo in repos:
        print(repo.status().format())
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    root_dir = find_root(args.root)
    manifest = Manifest.load()
    repos = _repos_from_manifest(manifest, root_dir)
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        log("Dry-run: showing pull operations that would run on all repositories...")
    else:
        log("Pulling all repositories...")
    _run_parallel(repos, lambda repo: repo.pull(dry_run=dry_run), jobs=args.jobs, description="pull")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    root_dir = find_root(args.root)
    manifest = Manifest.load()
    repos = _repos_from_manifest(manifest, root_dir)
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        log("Dry-run: showing push operations that would run on all repositories...")
    else:
        log("Pushing all repositories...")
    _run_parallel(repos, lambda repo: repo.push(dry_run=dry_run), jobs=args.jobs, description="push")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    root_dir = find_root(args.root)
    manifest = Manifest.load()
    repos = _repos_from_manifest(manifest, root_dir)
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        log("Dry-run: showing sync operations that would run on all repositories...")
    else:
        log("Syncing all repositories (pull then push)...")
    _run_parallel(repos, lambda repo: repo.sync(dry_run=dry_run), jobs=args.jobs, description="sync")
    return 0


def cmd_checkout(args: argparse.Namespace) -> int:
    root_dir = find_root(args.root)
    branch = args.branch
    manifest = Manifest.load()
    repos = _repos_from_manifest(manifest, root_dir)
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        log(f"Dry-run: would checkout branch {branch} on root and all child repos...")
    else:
        log(f"Checking out branch {branch} on root and all child repos...")
        try:
            run_command(["git", "checkout", branch], cwd=root_dir)
        except subprocess.CalledProcessError:
            log(f"Could not checkout {branch} on root repo", level="warning")
    _run_parallel(repos, lambda repo: repo.checkout(branch, dry_run=dry_run), jobs=args.jobs, description="checkout")
    return 0


def cmd_branch(args: argparse.Namespace) -> int:
    root_dir = find_root(args.root)
    manifest = Manifest.load()
    repos = _repos_from_manifest(manifest, root_dir)
    action = args.action
    dry_run = getattr(args, "dry_run", False)

    if action == "list":
        for repo in repos:
            branch = repo.current_branch() if repo.is_git_repo() else "not cloned"
            log(f"{repo.name}: {branch}")
        return 0

    if action == "create":
        if not args.name:
            log("Branch name is required for create", level="error")
            return 1
        if dry_run:
            log(f"Dry-run: would create branch {args.name} on all repositories...")
        else:
            log(f"Creating branch {args.name} on all repositories...")
        _run_parallel(repos, lambda repo: repo.create_branch(args.name, dry_run=dry_run), jobs=args.jobs, description="create branch")
        return 0

    if action == "delete":
        if not args.name:
            log("Branch name is required for delete", level="error")
            return 1
        if dry_run:
            log(f"Dry-run: would delete branch {args.name} on all repositories...")
        else:
            log(f"Deleting branch {args.name} on all repositories...")
        _run_parallel(repos, lambda repo: repo.delete_branch(args.name, dry_run=dry_run), jobs=args.jobs, description="delete branch")
        return 0

    log(f"Unknown branch action: {action}", level="error")
    return 1


def cmd_exec(args: argparse.Namespace) -> int:
    """Execute a shell command in all child repositories."""
    root_dir = find_root(args.root)
    manifest = Manifest.load()
    repos = _repos_from_manifest(manifest, root_dir)
    command = args.command
    if not command:
        log("No command provided. Example: fgit exec -- npm install", level="error")
        return 1

    # argparse.REMAINDER may include the '--' separator; drop it.
    if command[0] == "--":
        command = command[1:]
        if not command:
            log("No command provided. Example: fgit exec -- npm install", level="error")
            return 1

    command_str = " ".join(command)
    log(f"Executing '{command_str}' in all child repositories...")

    def run_in_repo(repo: Repo) -> None:
        if not repo.exists():
            raise FileNotFoundError(f"{repo.name} is not cloned")
        if not repo.is_git_repo():
            raise RuntimeError(f"{repo.name} is not a git repo")
        result = subprocess.run(
            command,
            cwd=repo.path,
            text=True,
            capture_output=True,
        )
        if result.stdout:
            for line in result.stdout.splitlines():
                print(f"[{Colors.CYAN}{repo.name}{Colors.RESET}] {line}")
        if result.stderr:
            for line in result.stderr.splitlines():
                print(
                    f"[{Colors.CYAN}{repo.name}{Colors.RESET}] {Colors.RED}{line}{Colors.RESET}",
                    file=sys.stderr,
                )
        if result.returncode != 0:
            raise RuntimeError(f"exit code {result.returncode}")

    _run_parallel(repos, run_in_repo, jobs=args.jobs, silent=True, description=f"exec '{command_str}'")
    return 0


def cmd_sync_from(args: argparse.Namespace) -> int:
    """Sync the current branch by pulling and merging another branch from origin."""
    root_dir = find_root(args.root)
    manifest = Manifest.load()
    repos = _repos_from_manifest(manifest, root_dir)
    branch = args.branch
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        log(f"Dry-run: showing sync-from {branch} operations that would run...")
    else:
        log(f"Syncing from {branch} into current branches of all child repositories...")
    _run_parallel(repos, lambda repo: repo.sync_from(branch, dry_run=dry_run), jobs=args.jobs, description="sync-from")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Get or set the global fgit configuration."""
    cfg = load_config()

    if args.key == "root":
        if args.value is None:
            if cfg.default_root:
                print(cfg.default_root)
                return 0
            log("No default root configured. Set one with: fgit config root /path/to/root", level="warning")
            return 1
        cfg.default_root = args.value
        cfg.save()
        log(f"Default root set to: {args.value}", level="success")
        return 0

    log(f"Unknown config key: {args.key}", level="error")
    return 1


def cmd_project(args: argparse.Namespace) -> int:
    """Manage multiple fgit projects."""
    cfg = load_config()
    action = args.action

    if action == "list":
        if not cfg.projects:
            log("No projects configured. Add one with: fgit project add <name> <path>", level="warning")
            return 0
        log("Configured projects:")
        for name, path in sorted(cfg.projects.items()):
            marker = "*" if name == cfg.active_project else " "
            print(f"  [{marker}] {name}: {path}")
        return 0

    if action == "add":
        if not args.name or not args.path:
            log("Usage: fgit project add <name> <path>", level="error")
            return 1
        path = os.path.abspath(args.path)
        if not os.path.isdir(os.path.join(path, ".fgit")):
            log(f"{path} does not look like an fgit root (no .fgit directory)", level="warning")
        cfg.projects[args.name] = path
        # If this is the first project, make it active automatically.
        if not cfg.active_project:
            cfg.active_project = args.name
        cfg.save()
        log(f"Added project '{args.name}' -> {path}", level="success")
        return 0

    if action == "remove":
        if not args.name:
            log("Usage: fgit project remove <name>", level="error")
            return 1
        if args.name not in cfg.projects:
            log(f"Unknown project: {args.name}", level="error")
            return 1
        del cfg.projects[args.name]
        if cfg.active_project == args.name:
            cfg.active_project = next(iter(cfg.projects), None)
        cfg.save()
        log(f"Removed project '{args.name}'", level="success")
        return 0

    if action == "use":
        if not args.name:
            log("Usage: fgit project use <name>", level="error")
            return 1
        if args.name not in cfg.projects:
            log(f"Unknown project: {args.name}", level="error")
            return 1
        cfg.active_project = args.name
        cfg.save()
        log(f"Active project set to '{args.name}' -> {cfg.projects[args.name]}", level="success")
        return 0

    if action == "current":
        if cfg.active_project and cfg.active_project in cfg.projects:
            print(f"{cfg.active_project}: {cfg.projects[cfg.active_project]}")
            return 0
        if cfg.default_root:
            print(f"default: {cfg.default_root}")
            return 0
        log("No active project configured", level="warning")
        return 1

    log(f"Unknown project action: {action}", level="error")
    return 1


def cmd_credential(args: argparse.Namespace) -> int:
    """Set or show fgit credentials stored in ~/.config/fgit/netrc."""
    action = args.action

    if action == "set":
        username = args.username or input("GitLab username: ")
        password = args.password or getpass.getpass("GitLab password: ")
        creds.save_credentials(args.machine, username, password)
        return 0

    if action == "show":
        for machine, login, password in creds._netrc_lines():
            if not args.machine or machine == args.machine:
                masked = "*" * len(password) if password else ""
                print(f"{machine}: {login} / {masked}")
        return 0

    if action == "encrypt":
        creds.encrypt_netrc()
        return 0

    if action == "decrypt":
        creds.decrypt_netrc()
        return 0

    log(f"Unknown credential action: {action}", level="error")
    return 1


def cmd_remote(args: argparse.Namespace) -> int:
    """Manage remote URLs of child repositories."""
    root_dir = find_root(args.root)
    manifest = Manifest.load()
    repos = _repos_from_manifest(manifest, root_dir)
    action = args.action

    if action == "use-https":
        log("Switching all child repositories to HTTPS remote URLs...")
        _run_parallel(repos, lambda repo: repo.use_https_remote(), jobs=args.jobs, description="use-https")
        return 0

    if action == "show":
        for repo in repos:
            if repo.is_git_repo():
                url = repo._git_capture(["git", "remote", "get-url", "origin"], cwd=repo.path, use_credentials=False)
                log(f"{repo.name}: {url}")
            else:
                log(f"{repo.name}: not cloned", level="warning")
        return 0

    log(f"Unknown remote action: {action}", level="error")
    return 1


def cmd_clean(args: argparse.Namespace) -> int:
    """Remove untracked files from all child repositories."""
    root_dir = find_root(args.root)
    manifest = Manifest.load()
    repos = _repos_from_manifest(manifest, root_dir)
    dry_run = not args.force

    if dry_run:
        log("Dry-run: showing files that would be removed. Use --force to actually delete.")
    else:
        log("Removing untracked files from all child repositories...")

    for repo in repos:
        if not repo.is_git_repo():
            log(f"{repo.name}: not cloned", level="warning")
            continue
        try:
            output = repo.clean(dry_run=dry_run, ignored=args.ignored)
            if output:
                for line in output.splitlines():
                    print(f"[{Colors.CYAN}{repo.name}{Colors.RESET}] {line}")
            elif not dry_run:
                log(f"{repo.name}: nothing to remove", level="success")
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            log(f"{repo.name}: {exc}", level="error")
    return 0


def cmd_ssh_key(args: argparse.Namespace) -> int:
    """Generate an SSH key pair for Git host authentication, or show an existing one."""
    action = args.action

    if action == "generate":
        try:
            priv_path = ssh.generate_key(args.host, key_type=args.type, force=args.force)
        except FileExistsError as exc:
            log(str(exc), level="error")
            return 1
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            log(f"Failed to generate SSH key: {exc}", level="error")
            return 1

        log(f"Generated SSH key at {priv_path}", level="success")

        if ssh.add_ssh_config_entry(args.host, priv_path):
            log(f"Added Host entry for {args.host} to ~/.ssh/config", level="success")
        else:
            log(f"~/.ssh/config already has a Host entry for {args.host}; left untouched", level="warning")

        log("Add this public key to your Git host (e.g. GitLab > Settings > SSH Keys):")
        print(ssh.read_public_key(priv_path))
        return 0

    if action == "show":
        priv_path = ssh.key_path(args.host, args.type)
        if not os.path.isfile(priv_path + ".pub"):
            log(f"No key found at {priv_path}. Generate one with: fgit ssh-key generate", level="error")
            return 1
        print(ssh.read_public_key(priv_path))
        return 0

    log(f"Unknown ssh-key action: {action}", level="error")
    return 1


EXPLANATIONS = {
    "clone": "Clone the root repository, read .fgit/manifest.json, then clone every child repository listed in the manifest as a sibling directory of root.",
    "bootstrap": "If root already exists, clone any child repositories from the manifest that are missing on disk.",
    "init": "Scan sibling directories around root that contain a .git directory and build (or update) .fgit/manifest.json. Use --add-missing to auto-include newly discovered repos.",
    "status": "Show the current branch, dirty state, and ahead/behind counts for every child repository in the manifest.",
    "pull": "Run `git pull --ff-only origin <current-branch>` in every child repository.",
    "push": "Run `git push origin <current-branch>` in every child repository.",
    "sync": "Run pull followed by push in every child repository.",
    "checkout": "Checkout the given branch on root and every child repository. If the branch does not exist locally, create a tracking branch from origin/<branch>.",
    "branch": "Manage branches across all child repositories: list, create (from origin/<default-branch>), or delete.",
    "exec": "Run an arbitrary shell command in every child repository and prefix the output with the repository name.",
    "sync-from": "For every child repository: (1) remember current branch, (2) fetch origin, (3) checkout the target branch and pull it, (4) checkout the original branch, (5) merge the target branch. If a merge conflict occurs, the merge is aborted.",
    "clean": "Remove untracked files from every child repository. Runs as dry-run by default; use --force to actually delete.",
    "doctor": "Check every child repository for common issues: missing clone, not a git repo, remote URL mismatch, unexpected branch, dirty working tree.",
    "remote": "Show or switch origin remote URLs of child repositories. `use-https` converts SSH URLs to HTTPS.",
    "project": "Register and switch between multiple fgit projects. After `fgit project use <name>`, every fgit command uses that project's root by default.",
    "credential": "Store GitLab HTTPS credentials in ~/.config/fgit/netrc. Optionally encrypt with GPG.",
    "ssh-key": "Generate an SSH key pair for a Git host, register it in ~/.ssh/config, and print the public key to add on the host (e.g. GitLab > Settings > SSH Keys).",
    "config": "Get or set the legacy default_root path.",
}


def cmd_explain(args: argparse.Namespace) -> int:
    """Explain what a fgit command does without running it."""
    command = args.command
    explanation = EXPLANATIONS.get(command)
    if explanation:
        print(f"{command}: {explanation}")
        return 0
    log(f"No explanation available for '{command}'.", level="warning")
    print("Available commands: " + ", ".join(sorted(EXPLANATIONS)))
    return 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the health of all child repositories in the manifest."""
    root_dir = find_root(args.root)
    manifest = Manifest.load()
    repos = _repos_from_manifest(manifest, root_dir)

    log(f"Checking {len(repos)} repositories...")
    any_issue = False
    for repo in repos:
        health = repo.check_health()
        if health["ok"]:
            log(f"{repo.name}: healthy", level="success")
        else:
            any_issue = True
            log(f"{repo.name}: {len(health['issues'])} issue(s)", level="error")
            for issue in health["issues"]:
                log(f"    - {issue}")

    if any_issue:
        log("Some repositories have issues. Fix them and re-run `fgit doctor`.", level="warning")
        return 1
    log("All repositories are healthy.", level="success")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fgit",
        description="fgit — a multi-repo Git workflow helper.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "-r", "--root", default=None, help="Path to the fgit root repository."
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=DEFAULT_JOBS, help="Number of parallel jobs."
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # clone
    p_clone = sub.add_parser("clone", help="Clone root and all child repositories.")
    p_clone.add_argument("url", help="Git URL of the root repository.")
    p_clone.add_argument("dest", nargs="?", help="Destination directory for root.")
    p_clone.set_defaults(func=cmd_clone)

    # bootstrap
    p_bootstrap = sub.add_parser(
        "bootstrap", help="Clone child repositories from an existing root's manifest."
    )
    p_bootstrap.set_defaults(func=cmd_bootstrap)

    # init
    p_init = sub.add_parser("init", help="Create a manifest from sibling repositories.")
    p_init.add_argument(
        "--from-siblings", action="store_true", default=True, help="Scan sibling repos."
    )
    p_init.add_argument(
        "--default-branch", default="dev", help="Default branch for new manifest entries."
    )
    p_init.add_argument(
        "--add-missing", action="store_true", help="Auto-add discovered sibling repos that are not in the manifest."
    )
    p_init.set_defaults(func=cmd_init)

    # status
    p_status = sub.add_parser("status", help="Show status of all repositories.")
    p_status.set_defaults(func=cmd_status)

    # pull
    p_pull = sub.add_parser("pull", help="Pull current branch in all repositories.")
    p_pull.add_argument("--dry-run", action="store_true", help="Show what would be pulled without changing anything.")
    p_pull.set_defaults(func=cmd_pull)

    # push
    p_push = sub.add_parser("push", help="Push current branch in all repositories.")
    p_push.add_argument("--dry-run", action="store_true", help="Show what would be pushed without changing anything.")
    p_push.set_defaults(func=cmd_push)

    # sync
    p_sync = sub.add_parser("sync", help="Pull then push current branch in all repositories.")
    p_sync.add_argument("--dry-run", action="store_true", help="Show what would be synced without changing anything.")
    p_sync.set_defaults(func=cmd_sync)

    # checkout
    p_checkout = sub.add_parser("checkout", help="Checkout a branch in root and all child repos.")
    p_checkout.add_argument("branch", help="Branch name to checkout.")
    p_checkout.add_argument("--dry-run", action="store_true", help="Show what would be checked out without changing anything.")
    p_checkout.set_defaults(func=cmd_checkout)

    # branch
    p_branch = sub.add_parser("branch", help="Manage branches across repositories.")
    p_branch.add_argument("action", choices=["list", "create", "delete"], help="Branch action.")
    p_branch.add_argument("name", nargs="?", help="Branch name (for create/delete).")
    p_branch.add_argument("--dry-run", action="store_true", help="Show what would be done without changing anything.")
    p_branch.set_defaults(func=cmd_branch)

    # exec
    p_exec = sub.add_parser("exec", help="Execute a shell command in all child repositories.")
    p_exec.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command and arguments to run in each child repository.",
    )
    p_exec.set_defaults(func=cmd_exec)

    # sync-from
    p_sync_from = sub.add_parser(
        "sync-from",
        help="Pull a branch from origin and merge it into the current branch of all child repositories.",
    )
    p_sync_from.add_argument("branch", help="Branch name to sync from.")
    p_sync_from.add_argument("--dry-run", action="store_true", help="Show the sync-from plan without changing anything.")
    p_sync_from.set_defaults(func=cmd_sync_from)

    # doctor
    p_doctor = sub.add_parser("doctor", help="Check the health of all child repositories.")
    p_doctor.set_defaults(func=cmd_doctor)

    # explain
    p_explain = sub.add_parser("explain", help="Explain what a fgit command does.")
    p_explain.add_argument("command", help="Command to explain (e.g. sync-from).")
    p_explain.set_defaults(func=cmd_explain)

    # config
    p_config = sub.add_parser("config", help="Manage global fgit configuration.")
    p_config.add_argument("key", choices=["root"], help="Configuration key to get or set.")
    p_config.add_argument("value", nargs="?", help="Value to set.")
    p_config.set_defaults(func=cmd_config)

    # project
    p_project = sub.add_parser("project", help="Manage multiple fgit projects.")
    p_project.add_argument(
        "action",
        choices=["list", "add", "remove", "use", "current"],
        help="Project action.",
    )
    p_project.add_argument("name", nargs="?", help="Project name.")
    p_project.add_argument("path", nargs="?", help="Project root path (for add).")
    p_project.set_defaults(func=cmd_project)

    # credential
    p_credential = sub.add_parser("credential", help="Manage Git credentials for HTTPS remotes.")
    p_credential.add_argument(
        "action",
        choices=["set", "show", "encrypt", "decrypt"],
        default="show",
        nargs="?",
        help="Credential action.",
    )
    p_credential.add_argument("--machine", default="gitlab.atomsolution.vn", help="Git host.")
    p_credential.add_argument("--username", help="Git username.")
    p_credential.add_argument("--password", help="Git password or token.")
    p_credential.set_defaults(func=cmd_credential)

    # ssh-key
    p_ssh_key = sub.add_parser("ssh-key", help="Generate an SSH key pair for Git host authentication.")
    p_ssh_key.add_argument(
        "action", choices=["generate", "show"], default="generate", nargs="?", help="ssh-key action."
    )
    p_ssh_key.add_argument("--host", default="gitlab.atomsolution.vn", help="Git host to configure the key for.")
    p_ssh_key.add_argument("--type", default="ed25519", choices=["ed25519", "rsa"], help="SSH key type.")
    p_ssh_key.add_argument("--force", action="store_true", help="Overwrite an existing key.")
    p_ssh_key.set_defaults(func=cmd_ssh_key)

    # remote
    p_remote = sub.add_parser("remote", help="Manage remote URLs of child repositories.")
    p_remote.add_argument(
        "action",
        choices=["use-https", "show"],
        default="show",
        nargs="?",
        help="Remote action.",
    )
    p_remote.set_defaults(func=cmd_remote)

    # clean
    p_clean = sub.add_parser("clean", help="Remove untracked files from child repositories.")
    p_clean.add_argument(
        "-f", "--force", action="store_true", help="Actually delete files (default is dry-run)."
    )
    p_clean.add_argument(
        "-x", "--ignored", action="store_true", help="Also remove ignored files."
    )
    p_clean.set_defaults(func=cmd_clean)

    return parser


def main() -> int:
    from fgit.utils import setup_logging

    setup_logging()
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        log(str(exc), level="error")
        return 1
    except subprocess.CalledProcessError as exc:
        log(f"Git command failed: {exc}", level="error")
        return 1
    except KeyboardInterrupt:
        log("Interrupted.", level="warning")
        return 130


if __name__ == "__main__":
    sys.exit(main())
