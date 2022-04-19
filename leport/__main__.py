import sys
import os
import typer
from pathlib import Path
from rich import print
from rich.prompt import Confirm, Prompt
from pydantic.errors import PydanticValueError
from leport.impl.config import get_config, set_config, get_config_file_path, set_config_file_path, DEFAULT_CONFIG
from leport.utils.cli import add_typer_with_alias
import leport.impl.db as db
import leport.impl.pkg as pkg
import leport.impl.pkgbuild as pkgbuild
from leport.impl.types.pkg import PkgName, PkgFile
from leport.impl.types.repos import RepoNotFoundError
import leport.commands.repos
from leport.utils.cli import command


def yes_or_no(question: str) -> bool:
    while True:
        print(question + f" (y/n)", end=" ")
        reply = input().lower().strip()
        if reply in ("y", "yes", "n", "no"):
            return reply in ("y", "yes")


app = typer.Typer(no_args_is_help=True)
add_typer_with_alias(
    app,
    leport.commands.repos.app,
    name="repos",
    alias="r",
    help="commands to manage repos"
)


@app.callback()
def _pre_command(ctx: typer.Context, config: Path = None):
    set_config_file_path(config)

    # if not 'init', validate that config dirs exist
    if ctx.invoked_subcommand != "init":
        # init must initialize the config instance itself
        set_config()

        cfg = get_config()
        err = False
        for dir in ["repos", "build", "pkgs"]:
            val = getattr(cfg.dirs, dir)
            if not val.exists():
                print(f"dirs.{dir}: invalid value, '{val}' does not exist")
                err = True
        if err:
            print("\nOne or more directories is missing, maybe you forgot to run the `init` command?")
            sys.exit(1)


@command(app, name="init")
def init():
    """Initialize configuration file and directories"""
    cfg_fpath = get_config_file_path()

    if not cfg_fpath.exists():
        if yes_or_no(f"No configuration file found at '{cfg_fpath}', create?"):
            with open(cfg_fpath, "w") as fh:
                for line in DEFAULT_CONFIG.splitlines():
                    print(line, file=fh)
        else:
            print("\nNo configuration file, aborting...")
            sys.exit(2)

    set_config()
    cfg = get_config()

    print("Based on the configuration file, the following settings would be used:")
    print(f"repos directory: '{cfg.dirs.repos}'")
    print(f"   (this is where local ports and git clones of upstream repositories will reside)")
    print(f"data directory: '{cfg.dirs.data}'")
    print(f"   This contains the database tracking installed files and packages")
    print(f"   as well as the build directory where data is stored temporarily while building packages.")
    print(f"pkgs directory: '{cfg.dirs.pkgs}'")
    print(f"   (this is where the finished packages will be built)")
    print()
    if not yes_or_no("proceed with these settings?"):
        print(f"OK, aborting, please edit the config at '{cfg_fpath}' and re-run `init`")
        sys.exit(0)

    # create directories, if missing.
    for key in ["repos", "data", "build", "pkgs"]:
        val = getattr(cfg.dirs, key)
        if not val.exists():
            val.mkdir(parents=True)

    # finally, initialize the package database
    db.init_db()


# TODO: remove
@app.command()
def dbclear():
    import leport.impl.db as db
    from pypika import Query, Table
    t_pkg = Table("pkgs")
    t_files = Table("files")
    with db.get_conn() as conn:
        conn.execute(str(Query.from_(t_pkg).delete()))
        conn.execute(str(Query.from_(t_files).delete()))


@app.command()
def dbreset():
    import leport.impl.db as db
    with db.get_conn() as conn:
        conn.execute(db.q_drop_table("pkgs", if_exists=True))
        conn.execute(db.q_create_pkg_table())
        conn.execute(db.q_drop_table("files", if_exists=True))
        conn.execute(db.q_create_files_table())


@command(app, name="search", alias="s", no_args_is_help=True)
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


@command(app, name="build", alias="b", no_args_is_help=True)
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
def install(pkg_fpath: Path = typer.Argument(..., metavar="pkg", exists=True, file_okay=True, readable=True, help="path to packagefile"),
            force: bool = typer.Option(default=False, help="automatically accept package overwrites")):
    """Install binary package"""
    print("INstALL")
    try:
        pkg_file = PkgFile(path=pkg_fpath)
    except PydanticValueError as e:
        print(e)
        sys.exit(1)

    if os.geteuid():
        print("[bold red]> You need root privileges for this action, use sudo")
        sys.exit(1)

    with db.get_conn() as conn:
        warned = False

        conflicts = []
        for file in pkg.install_conflicts(pkg_file):
            # display one-time warning
            if warned is False and force is False:
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
def remove(pkg_name: str = typer.Argument(..., metavar="pkg", help="name of package, either '<pkg>' or qualified '<repo>/<pkg>'")):
    """Remove package from system"""
    print("remove")
    if os.geteuid():
        print("[bold red] You need root privileges for this action, use sudo")
        sys.exit(1)
    pkg.remove(get_config(), PkgName.from_str(pkg_name))


@command(app, name="owns", alias="o", no_args_is_help=True)
def owns(file: Path = typer.Argument(..., help="check which package (if any) owns this file")):
    """Query which package (if any) owns a given file."""
    conn = db.get_conn()
    pkg = db.which_pkg_owns_file(conn, str(file))
    if pkg is None:
        print("[yellow bold]No package owns this, likely provided by an OS-package, possibly an orphan")
        sys.exit(2)
    else:
        print(f"[bold green]{pkg}")
        sys.exit(1)


@command(app, name="list-files", alias="ls", no_args_is_help=True)
def list_files(pkg_name: str = typer.Argument(..., metavar="pkg", help="TODO TODO")):
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


def main():
    app()


if __name__ == "__main__":
    main()