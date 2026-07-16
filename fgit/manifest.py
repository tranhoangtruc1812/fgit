"""Manifest loading and persistence for fgit."""
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fgit.utils import DEFAULT_BRANCH, MANIFEST_DIR, MANIFEST_FILE, find_root


@dataclass
class RepoEntry:
    """A single repository entry in the manifest."""

    name: str
    url: str
    branch: str = DEFAULT_BRANCH
    path: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RepoEntry":
        return cls(
            name=data["name"],
            url=data["url"],
            branch=data.get("branch", DEFAULT_BRANCH),
            path=data.get("path"),
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"name": self.name, "url": self.url}
        if self.branch != DEFAULT_BRANCH:
            result["branch"] = self.branch
        if self.path:
            result["path"] = self.path
        return result


@dataclass
class Manifest:
    """The fgit manifest describing the root repo and its children."""

    version: str = "1.0"
    default_branch: str = DEFAULT_BRANCH
    layout: str = "sibling"
    repos: List[RepoEntry] = field(default_factory=list)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Manifest":
        """Load a manifest from the given path or from the discovered root."""
        if path is None:
            root_dir = find_root()
            path = os.path.join(root_dir, MANIFEST_DIR, MANIFEST_FILE)
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls(
            version=data.get("version", "1.0"),
            default_branch=data.get("default_branch", DEFAULT_BRANCH),
            layout=data.get("layout", "sibling"),
            repos=[RepoEntry.from_dict(r) for r in data.get("repos", [])],
        )

    def save(self, path: Optional[str] = None) -> None:
        """Persist the manifest to disk."""
        if path is None:
            root_dir = find_root()
            manifest_dir = os.path.join(root_dir, MANIFEST_DIR)
            os.makedirs(manifest_dir, exist_ok=True)
            path = os.path.join(manifest_dir, MANIFEST_FILE)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "default_branch": self.default_branch,
            "layout": self.layout,
            "repos": [r.to_dict() for r in self.repos],
        }

    def repo_path(self, repo: RepoEntry, root_dir: str) -> str:
        """Resolve the absolute path for a repo entry."""
        if self.layout == "sibling":
            return os.path.abspath(os.path.join(root_dir, "..", repo.name))
        if repo.path:
            return os.path.abspath(os.path.join(root_dir, repo.path))
        return os.path.abspath(os.path.join(root_dir, repo.name))


def default_manifest() -> Manifest:
    """Return a default manifest with a sensible layout."""
    return Manifest(version="1.0", default_branch=DEFAULT_BRANCH, layout="sibling")
