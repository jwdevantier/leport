from pathlib import Path
from typing import Union
from pydantic import BaseModel, Extra, Field, root_validator


class UnknownRepoError(Exception):
    def __init__(self, repo: str):
        self.repo = repo
        super().__init__(f"Unknown repo '{repo}'")


class RepoNotFoundError(Exception):
    def __init__(self, repo_name: str):
        self.repo_name = repo_name
        super().__init__(f"repo '{repo_name}' does not exist")


class BaseRepo(BaseModel):
    name: str

    def repo_dir(self, config: "Config") -> Path:
        return config.dirs.repos / self.name

    class Config:
        extra = Extra.allow


class LocalRepo(BaseRepo):
    # do not accept extra keys - avoids coercing non-compliant git entry into a local repo entry
    class Config:
        extra = Extra.forbid


class GitRepo(BaseRepo):
    git: str = Field(description="url/path to git repository")
    branch: str = Field(description="branch to use", default=None)
    tag: str = Field(description="tag to use", default=None)

    @root_validator
    def ensure_branch_or_tag(cls, values):
        branch = values.get("branch")
        tag = values.get("tag")
        if branch is not None and tag is not None:
            raise ValueError("cannot specify BOTH branch and tag")
        if tag is None and branch is None:
            # default to using master branch
            values["branch"] = "master"

        return values


Repo = Union[LocalRepo, GitRepo]
