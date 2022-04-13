import functools
import sys
import os
from typing import Optional
from pathlib import Path
import typer
import click
from rich import print
from rich.prompt import Confirm
from rich.pretty import pprint
from pydantic.errors import PydanticValueError
from leport.impl.config import set_leport_root, get_leport_root, load_config, get_config, DEFAULT_CONFIG
import leport.impl.db as db
import leport.impl.pkg as pkg
import leport.impl.pkgbuild as pkgbuild
from leport.impl.types.pkg import PkgName, PkgFile
from leport.impl.types.repos import RepoNotFoundError
from leport.impl.repos import refresh_repos
from leport.utils.cli import command
from leport.utils.fileutils import sh, group_info, current_group
from leport.utils.errors import Error


class NaturalOrderGroup(click.Group):
    def list_commands(self, ctx):
        return self.commands.keys()


app = typer.Typer(cls=NaturalOrderGroup, no_args_is_help=True)


@app.callback()
def _pre_command(ctx: typer.Context, root_dir: Path = None):
    # if not 'init', validate that config dirs exist
    if ctx.invoked_subcommand != "init":
        # init must initialize the config object itself.
        set_leport_root(root_dir)
        load_config()

    # ensure perms default to:
    # directories:  775
    # files:        664
    os.umask((0o666 - 0o664))
    return


def require_leport_group(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        group_name = "leport"
        leport_group = group_info(group_name)
        if leport_group is None:
            print(f"Leport uses group [cyan]{group_name}[/cyan] to allow multiple users to use the program")
            print("to manage ports. But the group was not found on your system, try to run the")
            print("`init` command to properly initialize the system")
            sys.exit(1)

        cg = current_group()

        if cg.gr_name != group_name:
            print(f"Your current shell group is [cyan]{cg.gr_name}[/cyan], while Leport")
            print(f"expects the group [cyan]{group_name}[/cyan] which owns the shared state")
            print(f"and configuration files in {str(get_leport_root())}.")
            print("")
            print(f"Please change to the proper group, e.g. by calling `newgrp {group_name}`")
            print("before running the leport command.")
            sys.exit(1)
        return fn(*args, **kwargs)
    return wrapper


def require_root(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if os.geteuid():
            print("[bold red]> You need root privileges for this action, use sudo")
            sys.exit(1)
        return fn(*args, **kwargs)
    return wrapper


@command(app, name="init")
@require_root
def init():
    """Initialize configuration file and directories"""

    group_name = "leport"
    leport_group = group_info(group_name)
    if leport_group is None:
        print("Leport uses a group to allow multiple non-root users to administrate system ports.")
        if not Confirm.ask("Create group [cyan]leport[/cyan] ?"):
            print("OK, aborting.")
            sys.exit(1)
        sh("groupadd", group_name)

    root = get_leport_root()
    cfg_fpath = root / "config.yml"

    print(f"LEPORT_ROOT: {root}")
    print("")
    print("Continuing initialization will create and populate the directory")
    print(f"at '{root}'.")
    print("")
    print("The root directory is determined by (from highest to lowest priority):")
    print("1) the `--root-dir` command-line parameter")
    print("2) the LEPORT_ROOT environment variable")
    print("3) the default path of '/opt/leport'")

    if not Confirm.ask("Continue with initialization of root directory?"):
        print("OK, aborting.")
        sys.exit(0)

    for dir in [ root / "repos",
                 root / "data",
                 root / "data" / "registry",
                 root / "pkgs"]:
        dir.mkdir(parents=True, exist_ok=True)
        sh("chgrp", "-R", group_name, str(dir))

    if not cfg_fpath.exists():
        with open(cfg_fpath, "w") as fh:
            for line in DEFAULT_CONFIG.splitlines():
                print(line, file=fh)

    sh("chgrp", group_name, str(cfg_fpath))
    sh("chmod", "664", str(cfg_fpath))

    load_config()

    # finally, initialize the package database
    db.init_db()
    sh("chgrp", group_name, str(get_config().db_fpath))

    refresh_repos(get_config())


@app.command()
def dbreset():
    import leport.impl.db as db
    with db.get_conn() as conn:
        conn.execute(db.q_drop_table("pkgs", if_exists=True))
        conn.execute(db.q_drop_table("files", if_exists=True))
        conn.execute(db.q_drop_table("dirs", if_exists=True))

        conn.execute(db.q_create_pkgs_table())
        conn.execute(db.q_create_files_table())
        conn.execute(db.q_create_dirs_table())


@app.command()
def lolcaek(pkg: str):
    import leport.impl.db as db
    with db.get_conn() as conn:
        for fpath, count in db.pkg_dirs(conn, pkg):
            print(f"{fpath}\t=>\t{count}")


@command(app, name="search", alias="s", no_args_is_help=True)
@require_leport_group
def search(name: str = typer.Argument(..., help="name of package to search for"),
           dist: int = typer.Argument(2, help="accepted levenshtein distance, increase for more tolerant fuzzy search")):
    """Search for package among repositories"""
    matches = pkg.search(get_config(), name, max_l_dist=dist)
    if len(matches) == 0:
        print("No results, sorry")
        return

    print("\nResults:")
    for match in matches:
        print(f"[dim gray]* [yellow]{match.repo.name}[b white]/[bold magenta]{match.name}")


@command(app, name="packages", alias="pls", no_args_is_help=False)
@require_leport_group
def pkg_list():
    """List installed packages"""
    with db.get_conn() as conn:
        pkgs = conn.execute(db.q_pkgs_ls()).fetchall()
        if len(pkgs) == 0:
            print("[bold yellow]No packages installed")
            sys.exit(2)
        print("Installed Packages:")
        for pkg_name, version, release in pkgs:
            print(f"[bold white]* [magenta]{pkg_name} [/magenta]([blue]{version}-{release}[/blue])")


@command(app, name="build", alias="b", no_args_is_help=True)
@require_leport_group
def build(pkg_name: str = typer.Argument(..., metavar="pkg", help="name of package, either '<pkg>' or qualified '<repo>/<pkg>'"),
            clean: bool = typer.Option(True, help="if set, clean build directory and start from scratch")):
    """Build package from port recipe."""
    pkg_ = PkgName.from_str(pkg_name)
    try:
        pkg_dir = pkg.lookup(get_config(), pkg_)
        if pkg_dir is None:
            if pkg_.repo is None:
                print(f"Package '{pkg_.name}' not found in any of the repositories")
            else:
                print(f"Package '{pkg_.name}' not found in {pkg_.repo} repository")
            print("Consider using `search` command.")

        pkgbuild.build(pkg_dir, clean=clean)
    except RepoNotFoundError as e:
        print(f"[bold red]{str(e)}")
        sys.exit(1)


@command(app, name="install", alias="i", no_args_is_help=True)
@require_root
def install(pkg_fpath: Path = typer.Argument(..., metavar="pkg", exists=True, file_okay=True, readable=True, help="path to packagefile"),
            force: bool = typer.Option(default=False, help="automatically accept package overwrites")):
    """Install binary package"""
    try:
        pkg_file = PkgFile(path=pkg_fpath)
    except PydanticValueError as e:
        print(e)
        sys.exit(1)

    with db.get_conn() as conn:
        warned = False

        info = pkg.extract_info(pkg_file)
        if db.has_pkg(conn, info.name):
            print(f"[bold red]Already got a package named '{info.name}' installed, to upgrade, first remove it, then install")
            sys.exit(2)

        conflicts = []
        for file in pkg.install_conflicts(pkg_file):
            if force:
                continue
            # display one-time warning
            if warned is False:
                print("[yellow]File conflict detected - one or more files in the package conflict with existing files[/yellow]")
                print("")
                print("You will be asked whether to overwrite the existing file for each conflict found.")
                print("Finally, you will be asked whether to continue at the end. You may also press [magenta]CTRL-C[/magenta] now to abort\n")
                warned = True

            # display message asking whether to overwrite existing file or not.
            src_pkg = db.which_pkg_owns_file(conn, str(file))
            if src_pkg:
                msg = f"""[bold blue]{file}[/bold blue]: exists, provided by '{src_pkg}' package, overwrite?"""
            else:
                msg = f"""[bold blue]{file}[/bold blue]: exists, but is not from a ports package, may be owned by OS, may be an orphan file, overwrite?"""
            conflicts.append((file, Confirm.ask(msg)))

    # if any conflicts detected, summarize actions to be taken
    if conflicts:
        print("\n[bold magenta]Summarizing choices:[/bold magenta]:")
        for f, overwrite in conflicts:
            if overwrite:
                print(f"[bold blue]{f}[/bold blue]: [bold red]overwrite[/bold red]")
            else:
                print(f"[bold blue]{f}[/bold blue]: [bold white]keep[/bold white]")
        if not Confirm.ask("Continue?"):
            sys.exit(0)

    # TODO: abort if package is installed -- later support upgrade.
    pkg.install(get_config(), pkg_file, conflicts)


@command(app, name="remove", alias="rm", no_args_is_help=True)
@require_root
def remove(pkg_name: str = typer.Argument(..., metavar="pkg", help="name of package, either '<pkg>' or qualified '<repo>/<pkg>'")):
    """Remove package from system"""
    print("remove")
    if os.geteuid():
        print("[bold red] You need root privileges for this action, use sudo")
        sys.exit(1)
    pkg.remove(get_config(), PkgName.from_str(pkg_name))


@command(app, name="which", alias="w", no_args_is_help=True)
@require_leport_group
def which(file: Path = typer.Argument(..., help="check which package (if any) owns this file")):
    """which package owns file? (if any)"""
    conn = db.get_conn()
    pkg = db.which_pkg_owns_file(conn, str(file))
    if pkg is None:
        print("[yellow bold]No package owns this, likely provided by an OS-package, possibly an orphan")
        sys.exit(2)
    else:
        print(f"[bold green]{pkg}")
        sys.exit(1)


@command(app, name="files", alias="fls", no_args_is_help=True)
@require_leport_group
def pkg_files(pkg_name: str = typer.Argument(..., metavar="pkg", help="TODO TODO")):
    """Query files owned by a given package"""
    conn = db.get_conn()
    files = db.pkg_files_installed(conn, pkg_name)
    if not files:
        # TODO: follow-up query, do we have a pkg entry, even...?
        print(f"[bold yellow]No files for package '{pkg_name}'")
        sys.exit(2)
    print(f"[bold magenta]Files for package [bold green]{pkg_name}")
    for fpath, hash in files:
        print(f"[bold blue]{str(fpath)}")


@command(app, name="refresh", alias="rr", no_args_is_help=False)
@require_leport_group
def repos_refresh(repo: Optional[str] = typer.Argument(None, help="repo to refresh (default: all repos)")):
    """refresh one or all git repos"""
    cfg = get_config()
    refresh_repos(cfg, repo)


@command(app, name="repos", alias="rls", no_args_is_help=False)
@require_leport_group
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


def main():
    try:
        app()
    except Error as e:
        print("[red]Fatal error occurred:")
        print("[red]---------------------")
        print("[yellow]Context:")
        pprint(e.context)
        print("\n")
        e.display_error()
        sys.exit(1)
