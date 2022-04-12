import sys
import os
from typing import List, Optional
from pathlib import Path
import yaml
from pydantic import BaseModel, Field, ValidationError, Extra, root_validator
from rich import print
from leport.impl.types.repos import Repo


LEPORT_ROOT = Path(os.environ.get("LEPORT_ROOT", "/opt/leport"))

DEFAULT_CONFIG = """\
repos:
  # no url given, implied that it is a local repository in `{dirs.repos}/ports`
  - name: ports
  # if uncommented, would point to an upstream git repository as a ports repository
  #- name: upstream
  #  url: https://github.com/<user>/my-ports
"""


class Config(BaseModel):
    root: Path = Field(default_factory=lambda: Path(LEPORT_ROOT))
    repos: List[Repo] = Field(default_factory=list)

    @root_validator
    def check_config_dir(cls, values):
        root = values.get("root")
        if root is None:
            raise ValueError("root must be a Path, cannot be None")
        elif not isinstance(root, Path):
            raise ValueError(f"root must be a Path, got {repr(root)} (type: {type(root)})")

        cfg = root / "config.yml"
        if not cfg.exists():
            raise ValueError("root directory missing 'config.yml' config file")

        def dir_validator(v: Path):
            if not v.exists():
                raise ValueError(f"{v}: does not exist")
            if not v.is_dir():
                raise ValueError(f"{v} is not a directory")

        dir_validator(root / "repos")
        dir_validator(root / "pkgs")
        dir_validator(root / "data")

        return values

    @property
    def repos_path(self) -> Path:
        return self.root / "repos"

    @property
    def pkgs(self) -> Path:
        return self.root / "pkgs"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def build(self) -> Path:
        return self.data / "build"

    @property
    def pkg_registry(self) -> Path:
        return self.data / "registry"

    @property
    def db_fpath(self) -> Path:
        """Path to the sqlite database."""
        return self.data / "db.sqlite"

    def repo_from_name(self, repo_name: str) -> Optional[Repo]:
        for r in self.repos:
            if r.name == repo_name:
                return r
        return None

    class Config:
        extra = Extra.forbid


state = {
}


def get_leport_root() -> Path:
    val = state.get("LEPORT_ROOT", None)
    if val is None:
        val = LEPORT_ROOT
        set_leport_root(val)
    return val


def set_leport_root(root: Path):
    if "LEPORT_ROOT" in state:
        raise RuntimeError("cannot change LEPORT_ROOT once set")
    state["LEPORT_ROOT"] = LEPORT_ROOT if root is None else root


def load_config() -> Config:
    if "config" in state:
        raise RuntimeError("cannot reload config")

    conf_fpath = state["LEPORT_ROOT"] / "config.yml"
    config_data = {}
    if conf_fpath.exists():
        config_data = yaml.load(conf_fpath.read_text(), Loader=yaml.Loader)

    try:
        state["config"] = Config(**config_data)
        return state["config"]
    except ValidationError as e:
        print(e)
        sys.exit(1)


def get_config() -> Config:
    c = state.get("config", None)
    if not c:
        raise RuntimeError("no config loaded, should be impossible")
    return c
