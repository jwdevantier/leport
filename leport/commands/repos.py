from typing import Optional
import sys
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


@command(app, name="list", alias="ls", no_args_is_help=False)
def repos_list():
    """List configured repos"""
    cfg = get_config()
    if len(cfg.repos) == 0:
        print("[bold yellow]No configured repositories")
        sys.exit(2)

    print("Configured software repositories:")
    for repo in cfg.repos:
        print(f"[bold white] * [bold][magenta]{repo.name}[/magenta]")
        print(f"    [bold]path: [blue]{repo.repo_dir(cfg)}[/blue]")
        print(f"""    [bold]type: [blue]{repo.repo_type}""")
