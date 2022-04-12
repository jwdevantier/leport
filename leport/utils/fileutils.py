import grp
import hashlib
import importlib.util
from urllib.request import urlretrieve
from urllib.parse import urlparse
from typing import Optional, Union, Generator
from types import ModuleType
from pathlib import Path
import pwd
from contextlib import contextmanager
import os
import glob
import subprocess
from rich.progress import Progress
from rich import print
import tempfile


def url_fname(url: str) -> str:
    return Path(urlparse(url).path).name


def fetch_with_progress(url: str, dest: Path, desc: str = ""):
    """Fetch file from remote url to `dest` while displaying a progress bar."""
    with Progress() as progress:
        dltask = progress.add_task(desc, total=100)

        def on_update_handler(_, read_size, total_fsize):
            progress.update(dltask, total=total_fsize, advance=read_size)

        urlretrieve(url, dest, on_update_handler)


def sha256sum(fname: Path, blk_size=4096) -> str:
    h = hashlib.sha256()
    with open(fname, "rb") as fh:
        for blk in iter(lambda: fh.read(blk_size), b""):
            h.update(blk)
        return h.hexdigest()


def load_module_from_path(mod_path: Path, mod_name: Optional[str] = None) -> ModuleType:
    spec = importlib.util.spec_from_file_location(mod_name or mod_path.name, mod_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sh(*cmd, capture=False, **kwargs) -> subprocess.CompletedProcess:
    if len(cmd) == 1 and isinstance(cmd[0], list):
        cmd = cmd[0]

    print(f"""$ [bold green]{" ".join(cmd)}""")

    if not "check" in kwargs:
        kwargs["check"] = True

    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE

    if not "encoding" in kwargs:
        kwargs["encoding"] = "utf-8"

    return subprocess.run(cmd, **kwargs)


@contextmanager
def cwd(path: Union[str, Path]):
    """Change working directory for the duration of the context manager"""

    curr_cwd = Path.cwd()
    if not isinstance(path, Path):
        if not isinstance(path, str):
            raise TypeError(f"expected path or string, got {type(path).__name__}")
        path = Path(path)
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(curr_cwd)


def walk(root_path: Union[str, Path], include_dirs: bool = False) -> Generator[Path, None, None]:
    """Recursively traverse `path`, yielding the resolved path to each file."""
    if include_dirs:
        def _walk(path: Path):
            for p in Path(path).iterdir():
                if p.is_dir():
                    yield from _walk(p)
                yield p.relative_to(root_path)
    else:
        def _walk(path: Path):
            for p in Path(path).iterdir():
                if p.is_dir():
                    yield from _walk(p)
                    continue
                yield p.relative_to(root_path)

    yield from _walk(root_path)


def temp_direntry(dir: Path) -> Path:
    """returns path to a temporary free filename"""
    for name in tempfile._get_candidate_names():
        if not (dir / name).exists():
            return dir / name


def user_home() -> Path:
    """return home directory of user, even when using sudo."""
    return Path(os.path.expanduser(
        "~"
        + (os.environ.get("SUDO_USER") or os.environ.get("USER"))))


def user_uid(name: str, missing_ok=True) -> Optional[int]:
    try:
        return pwd.getpwnam(name).pw_uid
    except KeyError as e:
        if missing_ok:
            return None
        raise RuntimeError(f"missing user '{name}'") from e


def group_info(name: str) -> Optional[grp.struct_group]:
    """Return group struct, iff group exists.

    Args:
        name: group name

    Returns:
        Group struct iff. group exists, None otherwise.
    """
    try:
        return grp.getgrnam(name)
    except KeyError:
        return None


def group_gid(name: str, missing_ok=True) -> Optional[int]:
    g = group_info(name)
    if g:
        return g.gr_gid
    if missing_ok:
        return None
    raise RuntimeError(f"missing group '{name}'")


def current_group() -> grp.struct_group:
    return grp.getgrgid(os.getegid())


_uid_cache = {}
_gid_cache = {}


def stat_set(path: Path,
         user: Union[str, int] = None,
         group: Union[str, int] = None,
         perms: int = None):

    if isinstance(user, str):
        val = _uid_cache.get(user, None)
        if val is None:
            val = user_uid(user)
            if val is None:
                # TODO: nicer error
                raise RuntimeError("no such user")
            _uid_cache[user] = val

    if isinstance(group, str):
        val = _gid_cache.get(group, None)
        if val is None:
            val = group_gid(group)
            if val is None:
                # TODO: nicer error
                raise RuntimeError("no such group")
            _gid_cache[group] = val

    args = {k: v for k, v in {"uid": user, "gid": group} if v is not None}
    if args:
        os.chown(path, **args, follow_symlinks=False)
    if perms:
        os.chmod(path, perms)


def get_paths(root_dir: Path, *patterns: str) -> Generator[Path, None, None]:
    """Compute paths.

    Args:
        root_dir: patterns are matched relative to this directory
        *patterns: list of strings using shell-style glob pattern syntax

    NOTE:
        * each path is only returned once, regardless of several patterns matching

    Returns:
        A generator yielding (p: Path, is_dir: bool) tuples, where p is a path
        relative to the `root_dir` for each file/directory matching a pattern.
    """
    matched = set()
    for pattern in patterns:
        for res in glob.glob(pattern, recursive=True, root_dir=root_dir):
            if res not in matched:
                matched.add(res)
                p = Path(res)
                if (root_dir / p).is_dir():
                    yield p, True
                else:
                    yield p, False
