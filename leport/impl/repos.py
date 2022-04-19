from typing import Optional
from leport.impl.config import Config
from leport.impl.types.repos import LocalRepo, GitRepo
import leport.utils.git as lgit


def refresh_local(cfg: Config, repo_entry: LocalRepo) -> None:
    repo_dir = repo_entry.repo_dir(cfg)
    if not repo_dir.exists():
        repo_dir.mkdir()


def refresh_git(cfg: Config, repo_entry: GitRepo) -> None:
    lgit.refresh_git(src=repo_entry.git,
                     branch=repo_entry.branch,
                     tag=repo_entry.tag,
                     dst=repo_entry.repo_dir(cfg))


def refresh_repos(cfg: Config, repo: Optional[str] = None) -> None:
    repos = cfg.repos if repo is None else [repo]

    for repo in repos:
        print(f"Refreshing {repo.name}...")
        if isinstance(repo, LocalRepo):
            refresh_local(cfg, repo)
        elif isinstance(repo, GitRepo):
            refresh_git(cfg, repo)

