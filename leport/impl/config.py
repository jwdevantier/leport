import sys
import os
from typing import List, Optional
from pathlib import Path
import yaml
from pydantic import BaseModel, Field, ValidationError, Extra
from rich import print
from leport.impl.types.repos import Repo
from leport.impl.types.validators import DirOrMissing
from leport.utils.fileutils import user_home


DEFAULT_CONFIG_PATH = user_home() / ".config" / "leport" / "config.yml"

DEFAULT_CONFIG = """\
dirs:
  # the directory where all other repository directories would be placed
  repos: ~/.leport/repos
  # the directory where temporary build files are placed
  build: ~/.leport/build
  # this is where built packages would be stored
  pkgs: ~/.leport/pkgs

repos:
  # no url given, implied that it is a local repository in `{dirs.repos}/ports`
  - name: ports
  # if uncommented, would point to an upstream git repository as a ports repository
  #- name: upstream
  #  url: https://github.com/<user>/my-ports
"""


class Dirs(BaseModel):
    repos: DirOrMissing = Field(default_factory=lambda: user_home() / ".leport" / "repos")
    data: DirOrMissing = Field(default_factory=lambda: user_home() / ".leport" / "data")
    pkgs: DirOrMissing = Field(default_factory=lambda: user_home() / ".leport" / "pkgs")

    @property
    def build(self) -> Path:
        return self.data / "build"

    @property
    def pkg_registry(self) -> Path:
        return self.data / "registry"

    class Config:
        extra = Extra.forbid


class Config(BaseModel):
    repos: List[Repo] = Field(default_factory=list)
    dirs: Dirs

    @property
    def db_fpath(self) -> Path:
        """Path to the sqlite database."""
        return self.dirs.data / "db.sqlite"

    def repo_from_name(self, repo_name: str) -> Optional[Repo]:
        for r in self.repos:
            if r.name == repo_name:
                return r
        return None


state = {
}


def set_config() -> None:
    config = get_config_file_path()

    config_data = {}
    if config.exists():
        config_data = yaml.load(config.read_text(), Loader=yaml.Loader)

    try:
        state["config"] = Config(**config_data)
        print(state["config"])
    except ValidationError as e:
        print(e)
        sys.exit(1)


def get_config() -> Config:
    c = state.get("config", None)
    if not c:
        raise RuntimeError("no config loaded, should be impossible")
    return c


def set_config_file_path(config_fpath: Path = None) -> None:
    state["config_file_path"] = DEFAULT_CONFIG_PATH if config_fpath is None else config_fpath


def get_config_file_path() -> Path:
    val = state.get("config_file_path", None)
    if val is None:
        raise RuntimeError("no config file path set, should be impossible")
    return val
