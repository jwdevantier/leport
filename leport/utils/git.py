from pathlib import Path
from typing import Union, Optional, List
import git
from leport.impl.config import Config


class GitRepoError(Exception):
    def __init__(self,
                 msg: str,
                 repo_path: Path):
        self.repo_path = repo_path
        super().__init__(msg)


class GitRepoInvalidTagError(GitRepoError):
    def __init__(self, repo_path: Path, tag: str):
        self.tag = tag
        super().__init__(f"tag '{tag}' not found in repository", repo_path)


class GitRepoInvalidBranchError(GitRepoError):
    def __init__(self, repo_path: Path, branch: str):
        self.branch = branch
        super().__init__(f"branch '{branch}' not found in repository", repo_path)


class GitBareRepoError(GitRepoError):
    pass


def head_tags(r: git.Repo) -> List[git.TagReference]:
    """return list of tags matching HEAD."""
    head = r.head
    return [
        tag for tag in r.tags
        if head.commit == tag.commit
    ]


def is_using_tag(r: git.Repo, t: Union[str, git.TagReference]) -> bool:
    """True iff. HEAD is tagged with given tag"""
    try:
        if isinstance(t, str):
            t = r.tags[t]
    except IndexError:
        return False

    return t.commit in [t.commit for t in head_tags(r)]


def refresh_git(*,
                src: Union[str, Path],
                branch: Optional[str] = None,
                tag: Optional[str] = None,
                dst: Path) -> None:
    """Refresh/checkout git repository and change to specified tag or branch.

    Note: Can specify tag OR branch. If neither are provided, assume the `master`
    branch is desired.

    Args:
        src: source/origin of the git repository
        branch: branch to check out (optional, can define `tag` instead)
        tag: tag to check out (optional, can define branch instead)
        dst: destination path of repository (~ where to check out the repo)

    Raises:
        git.exc.InvalidGitRepositoryError: if destination is a regular directory, file or similar.
        GitBareRepoError: raised iff. repository exists, but is a bare repo (i.e. no working copy)
        GitRepoInvalidTagError: raised iff. supplied tag to check out does not exist
        GitRepoInvalidBranchError: raised iff. supplied branch does not exist.

    Returns:
        None
    """
    if branch is not None and tag is not None:
        raise ValueError("cannot define both a `tag` and a `branch` to check out")

    new_repo = False
    if dst.exists():
        repo = git.Repo(dst)  # git.exc.InvalidGitRepositoryError
        if repo.bare:
            raise GitBareRepoError("bare repo", dst)

        # fetch changes
        for remote in repo.remotes:
            remote.fetch()
    else:
        new_repo = True
        repo = git.Repo.clone_from(src, dst)

    # repo entries EITHER have `branch` or `tag` set.
    if tag:
        try:
            tag_ref = repo.tags[tag]
        except IndexError:
            raise GitRepoInvalidTagError(dst, tag)
        if not is_using_tag(repo, tag_ref):
            repo.git.checkout(tag_ref)
    else:
        branch = branch or "master"
        try:
            repo.git.checkout(branch)
        except git.exc.GitCommandError as e:
            if e.command == ["git", "checkout", branch]:
                raise GitRepoInvalidBranchError(dst, branch)
            else:
                raise e
        if not new_repo:
            # pull in fetched updates to branch
            repo.remotes.origin.pull()
