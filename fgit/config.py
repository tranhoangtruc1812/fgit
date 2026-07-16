"""Global fgit configuration stored in ~/.config/fgit/config.json."""
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

CONFIG_DIR = os.path.expanduser("~/.config/fgit")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


@dataclass
class Config:
    """Global fgit configuration."""

    default_root: Optional[str] = None
    projects: Dict[str, str] = field(default_factory=dict)
    active_project: Optional[str] = None

    @classmethod
    def load(cls) -> "Config":
        if not os.path.isfile(CONFIG_FILE):
            return cls()
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data: Dict[str, Any] = json.load(fh)
        return cls(
            default_root=data.get("default_root"),
            projects=data.get("projects", {}),
            active_project=data.get("active_project"),
        )

    def save(self) -> None:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if self.default_root:
            result["default_root"] = self.default_root
        if self.projects:
            result["projects"] = dict(self.projects)
        if self.active_project:
            result["active_project"] = self.active_project
        return result

    def get_active_root(self) -> Optional[str]:
        """Return the root path of the active project, if any."""
        if self.active_project and self.active_project in self.projects:
            return self.projects[self.active_project]
        return self.default_root


def load_config() -> Config:
    return Config.load()
