from typing import Optional
from pathlib import Path
import typer
from leport.utils.cli import command
from leport.impl.repos import refresh_repos
from leport.impl.config import get_config
from rich import print


app = typer.Typer(no_args_is_help=True)


@command(app, name="refresh", alias="r", no_args_is_help=False)
def repos_refresh(repo: Optional[str] = typer.Argument(None, help="repo to refresh (default: all repos)")):
    """refresh one or all git repos"""
    print("TODO: refresh repo :vampire:")
    cfg = get_config()
    refresh_repos(cfg, repo)


@command(app, name="test", alias="t")
def test():
    import git
    # url = "https://github.com/OpenMPDK/FlexAlloc"
    # dest = Path("/tmp/flexalloc")
    url = "https://github.com/OpenMPDK/xNVMe/"
    dest = Path("/tmp/xnvme")

    if not dest.exists():
        r = git.Repo.clone_from(url, dest)
    else:
        r = git.Repo(dest)
        for remote in r.remotes:
            remote.fetch()
    #
    # print(r.head.name)
    # if not r.head.is_detached:
    #     print(r.active_branch)
    # else:
    #     print("DETACHED")
    #
    # # check out compute branch
    r.git.checkout("compute")
    # print("local branches")
    # print(r.branches)
    print("remote branches")
    for ref in r.remote().refs:
        print(f"  > {repr(ref)}")
        print(dir(ref))
    # print("tags")
    # for tag in r.tags:
    #     print(f"  > {tag}")
    # print("HEADS")
    # for head in r.heads:
    #     print(f"  > {head}")
    # # r.git.checkout("v0.0.29")
    #
    # # cannot work -- r.tags["v0.0.29"].checkout()
    # # r.heads["v0.0.29"].checkout()
    # print("TAG TEST")
    # t = r.tags["v0.0.29"]  # fail w IndexError if not exists
    # r.git.checkout(t)
    # print(t)

    # print(r.head)

    if not r.head.is_detached:
        print(r.active_branch)
    else:
        print("DETACHED")
    # print(r.git.status())
    print(repr(r.active_branch))
