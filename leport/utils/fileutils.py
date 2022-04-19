import hashlib
import importlib.util
from urllib.request import urlretrieve
from urllib.parse import urlparse
from typing import Optional, Union, Generator
from types import ModuleType
from pathlib import Path
from contextlib import contextmanager
import os
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


def walk(path: Union[str, Path]) -> Generator[Path, None, None]:
    """Recursively traverse `path`, yielding the resolved path to each file."""
    for p in Path(path).iterdir():
        if p.is_dir():
            yield from walk(p)
            continue
        yield p.resolve()


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