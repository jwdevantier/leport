import os
from abc import ABC, abstractmethod
import shutil
import stat
from typing import List, Optional, Generator, Tuple, Set
import tarfile
from pathlib import Path
from typing import Type, cast

import fuzzysearch

from leport.impl.config import Config
from leport.impl.types.repos import Repo, RepoNotFoundError
from leport.impl.types.pkg import PkgSearchMatch, PkgName, PkgDir, PkgFile, PkgInfo, PkgManifest, PkgHooks
from leport.utils.fileutils import load_module_from_path, temp_direntry, sha256sum, group_gid, user_uid
import leport.impl.db as db
from rich import print


def search(cfg: Config, pkgname: str, max_l_dist: int = 1) -> List[PkgSearchMatch]:
    """Return sorted list of potential package matches.

    Args:
        cfg: config
        pkgname: name of package to locate
        max_l_dist: maximum levenshtein distance tolerated.
            if 1 then once character difference is tolerated, and so forth.

    Returns:
        List of matches in sorted order, sorted first by repo order, secondly by
        levenshtein distance, from closest to fuzziest match.
    """
    matches = []
    for repo in cfg.repos:
        repo_matches = []
        dir = repo.repo_dir(cfg)
        if not dir.exists():
            continue
        for pkg_dir in (d for d in dir.iterdir() if d.is_dir()):
            m = fuzzysearch.find_near_matches(pkgname, pkg_dir.name, max_l_dist=max_l_dist)
            if len(m) != 0:
                repo_matches.append(PkgSearchMatch(
                    name=pkg_dir.name,
                    repo=repo,
                    dist=m[0].dist
                ))
        if repo_matches:
            # sort matches in repo in order of lowest levenshtein distance (closest match) to least matching
            repo_matches.sort(key=lambda o: o.dist)
        matches.extend(repo_matches)
    return matches


def get_pkg_dir(cfg: Config, pkg: str, repo: Repo) -> Optional[PkgDir]:
    """Look for package `pkg_name` inside the provided repository.

    Args:
        cfg: config
        pkg: name of the packge to look for
        repo: an instance representing the repository to look in

    Returns:
        A path to the package directory if it exists, None otherwise.
    """
    pkg_path = repo.repo_dir(cfg) / pkg
    if not pkg_path.exists():
        return None

    # will do a bunch of validation to determine that `pkg_path` is indeed a pkg dir
    return PkgDir(
        name=pkg,
        repo=repo,
        path=pkg_path
    )


def lookup(cfg: Config, pkg: PkgName) -> Optional[PkgDir]:
    """Look for package `pkg`.

    Look for `pkg`, if `pkg` is unqualified, i.e. not of form `<repo-name>/<pkg-name>`,
    where the repository to look in is made explicit, then all the repositories are
    searched through in order of their definition in the configuration file.

    Args:
        cfg: the config
        pkg: an instance representing a qualified- or unqualified package.

    Returns:
        A path to the package directory, if any matched, None otherwise.
    """
    if pkg.repo:
        repo = cfg.repo_from_name(pkg.repo)
        if repo is None:
            raise RepoNotFoundError(pkg.repo)

        return get_pkg_dir(cfg, pkg.name, repo)
    else:
        for repo in cfg.repos:
            pkg_dir = get_pkg_dir(cfg, pkg.name, repo)
            if pkg_dir:
                return pkg_dir
        return None


def extract_info(pkg: PkgFile) -> PkgInfo:
    with tarfile.open(pkg.path, "r:xz") as tar:
        with tar.extractfile("info.yml") as fh:
            return PkgInfo.from_yaml(fh.read().decode("utf-8"))


def extract_manifest(pkg: PkgFile) -> PkgManifest:
    with tarfile.open(pkg.path, "r:xz") as tar:
        with tar.extractfile("manifest.yml") as fh:
            return PkgManifest.from_yaml(fh.read().decode("utf-8"))


def load_pkg_hooks(pkg_name: str, hooks_path: Path) -> Type[PkgHooks]:
    if not hooks_path.exists():
        return PkgHooks  # essentially NOOP all hooks

    mod = load_module_from_path(
        hooks_path,
        f"""leport.pkgfile.{pkg_name.replace(" ", "_").replace("-", "_")}.hooks"""
    )
    if (not hasattr(mod, "Hooks")
        or not issubclass(mod.Hooks, PkgHooks)):
        raise ValueError("invalid hooks file, expects class `Hooks` inheriting from PkgHooks")
    return cast(Type[PkgHooks], mod.Hooks)


def install_conflicts(pkg: PkgFile) -> Generator[Path, None, None]:
    "yields paths of all the files in the package for which a file already exists on the system"
    manifest = extract_manifest(pkg)
    for fpath in manifest.file_checksums.keys():
        if fpath.exists():
            yield fpath


class ReversibleAction(ABC):
    @abstractmethod
    def apply(self):
        ...

    @abstractmethod
    def revert(self):
        ...


class RmFile(ReversibleAction):
    def __init__(self, actual: Path):
        if actual.exists() and not actual.is_file():
            raise ValueError("rm only works for files")

        self._actual = actual
        self._tmp = temp_direntry(actual.parent)
        actual.rename(self._tmp)

    def apply(self):
        self._tmp.unlink(missing_ok=True)

    def revert(self):
        self._tmp.rename(self._actual)


class RmTree(ReversibleAction):
    def __init__(self, actual: Path):
        if actual.exists() and not actual.is_dir():
            raise ValueError("rmtree only works for directories")

        self._actual = actual
        self._tmp = temp_direntry(actual.parent)
        actual.rename(self._tmp)

    def apply(self):
        if self._tmp.exists():
            shutil.rmtree(self._tmp)

    def revert(self):
        self._tmp.rename(self._actual)


class MkDir(ReversibleAction):
    def __init__(self, path: Path, **opts):
        self._path = path
        if self._path.exists():
            raise ValueError(f"cannot create dir at path {path}, file or directory already exists")
        self._tmp = temp_direntry(path.parent)
        self._tmp.mkdir(**opts)

    @property
    def tmp_path(self) -> Path:
        return self._tmp

    def apply(self):
        self._tmp.rename(self._path)

    def revert(self):
        if self._tmp.exists() and self._tmp.is_dir():
            shutil.rmtree(self._tmp)


class DeleteOnError(ReversibleAction):
    def __init__(self, path: Path):
        self._path = path

    def apply(self):
        pass

    def revert(self):
        if not self._path.exists():
            return
        if self._path.is_dir():
            shutil.rmtree(self._path)
        else:
            self._path.unlink()


class Chown(ReversibleAction):
    def __init__(self, path: Path, uid: int = -1, gid: int = -1):
        self._path = path
        s = path.stat()
        self._uid = uid
        self._uid_old = -1 if uid == -1 else s.st_uid
        self._gid = gid
        self._gid_old = -1 if gid == -1 else s.st_gid

    def apply(self):
        os.chown(self._path, uid=self._uid, gid=self._gid)

    def revert(self):
        os.chown(self._path, uid=self._uid_old, gid=self._gid_old)


class Chmod(ReversibleAction):
    mode_mask: int = stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO

    def __init__(self, path: Path, mode: int):
        self._path = path
        self._mode = mode
        m = path.stat().st_mode
        self._old_mode = m & self.mode_mask

    def apply(self):
        os.chmod(self._path, mode=self._mode)

    def revert(self):
        os.chmod(self._path, mode=self._old_mode)


class ReversibleFileActions(object):
    def __init__(self):
        self._actions: List[ReversibleAction] = []

    def rm(self, path: Path):
        self._actions.append(RmFile(path))

    def rmtree(self, path: Path):
        self._actions.append(RmTree(path))

    def mkdir(self, path: Path) -> Path:
        a = MkDir(path)
        self._actions.append(a)
        return a.tmp_path

    def delete_on_error(self, path: Path):
        self._actions.append(DeleteOnError(path))

    def chown(self, path: Path, *, uid: int = -1, gid: int = -1):
        self._actions.append(Chown(path, uid=uid, gid=gid))

    def chmod(self, path: Path, mode: int):
        self._actions.append(Chmod(path, mode))

    def __enter__(self):
        return self

    def __exit__(self, typ, value, traceback):
        failed_items = []
        actions = self._actions[::-1]
        if typ:
            for action in actions:
                try:
                    action.revert()
                except Exception:
                    failed_items.append(action)
                    pass
        else:
            for action in actions:
                try:
                    action.apply()
                except Exception:
                    failed_items.append(action)
        self._actions = failed_items[::-1]
        return


def install(config: Config, pkg: PkgFile, conflicts: List[Tuple[Path, bool]]):
    # TODO: track progress
    with ReversibleFileActions() as fs:
        with tarfile.open(pkg.path, "r:xz") as tar:
            with tar.extractfile("info.yml") as fh:
                info = PkgInfo.from_yaml(fh.read().decode("utf-8"))
            with tar.extractfile("manifest.yml") as fh:
                manifest = PkgManifest.from_yaml(fh.read().decode("utf-8"))

            install_md_dir = fs.mkdir((config.pkg_registry / info.name))
            leport_gid = group_gid("leport", missing_ok=False)

            # copy `info.yml`, `manifest.yml` and `hooks.py` (if it exists) into the
            # install metadata directory.
            with tar.extractfile("info.yml") as src:
                fpath = install_md_dir / "info.yml"
                with open(fpath, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                fs.chown(fpath, gid=leport_gid)
                fs.delete_on_error(fpath)
            with tar.extractfile("manifest.yml") as src:
                fpath = install_md_dir / "manifest.yml"
                with open(fpath, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                fs.chown(fpath, gid=leport_gid)
                fs.delete_on_error(fpath)
            if "hooks.py" in tar.getnames():
                with tar.extractfile("hooks.py") as src:
                    fpath = install_md_dir / "hooks.py"
                    with open(fpath, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    fs.chown(fpath, gid=leport_gid)
                    fs.delete_on_error(fpath)

            try:
                conn = db.get_conn()
                hooks = load_pkg_hooks(info.name, install_md_dir / "hooks.py")(info, manifest)
                for fpath, should_overwrite in conflicts:
                    if should_overwrite:
                        fs.rm(fpath)
                try:
                    hooks.preinst()
                except Exception as e:
                    # TODO: log
                    print("[yellow bold]preinst hook raised an unhandled error")
                exclude_files = {
                    fpath
                    for fpath, should_overwrite in conflicts
                    if should_overwrite is False
                }

                conn.execute("begin")

                # will contain dirs and files
                installed: Set[Path] = set()

                def members(tf: tarfile.TarFile):
                    # extract all entries under 'files/' and strip 'files/' from path
                    strip_prefix_len = len("files/")
                    for member in tf.getmembers():
                        if member.path.startswith("files/"):
                            member.path = member.path[strip_prefix_len:]
                            p = Path("/" + member.path)
                            if p.exists():
                                if p in exclude_files:
                                    continue
                                elif not p.is_dir():
                                    # must be overwritten
                                    # this operation slates file for deletion, this means the file is temporarily
                                    # moved, and deleted if overall install succeeds, otherwise restored.
                                    fs.rm(p)
                            else:
                                fs.delete_on_error(p)
                            installed.add(p)
                            yield member

                tar.extractall(path=Path("/"), members=members(tar))

                installed_files = [f for f in installed if not f.is_dir()]
                installed_dirs = [d for d in installed if d.is_dir()]

                # Now we verify that all installed files' sha256 checksum match those found in the package manifest.
                installed_files_checksums = {}

                for fpath in installed_files:
                    actual_hash = sha256sum(fpath)
                    installed_files_checksums[fpath] = actual_hash
                    try:
                        if manifest.file_checksums[fpath] != actual_hash:
                            raise RuntimeError(f"{fpath}: expected {manifest.file_checksums[fpath]}, got {actual_hash}")
                    except KeyError:
                        print(repr(manifest.file_checksums))
                        raise RuntimeError(f"{fpath}: no entry found in manifest!")

                for fpath in installed:
                    s = manifest.stat[fpath]
                    # No need to reverse these actions, files/dirs will be removed on failure
                    os.chmod(fpath, int(s.mode, base=8))
                    os.chown(fpath,
                             uid=user_uid(s.user, missing_ok=False),
                             gid=group_gid(s.group, missing_ok=False))

                # record entries for files which we've installed and whose hash matched the one in the manifest
                db.record_files(conn, info.name, PkgManifest(
                    file_checksums=installed_files_checksums,
                    stat=manifest.stat
                ))
                db.record_dirs(conn, info.name, installed_dirs)
                db.record_pkg(conn, info)

                try:
                    hooks.postinst()
                except Exception as e:
                    # TODO log
                    print("[yellow bold]postinst hook raised an unhandled error")
                conn.execute("commit")
            except Exception as e:
                conn.execute("rollback")
                shutil.rmtree(install_md_dir, ignore_errors=True)
                raise e


def remove(config: Config, name: PkgName):
    if name.repo is not None:
        raise ValueError("installed packages do not use repo prefix, e.g. `rm vim` not `rm <my-repo>/vim`")
    try:
        install_md_dir = (config.pkg_registry / name.name)
        if not install_md_dir.exists():
            raise ValueError("package not in registry")
        if not install_md_dir.is_dir():
            raise ValueError(f"registry entry '{install_md_dir}' is invalid, not a directory")
        info = PkgInfo.from_yaml(install_md_dir / "info.yml")
        manifest = PkgManifest.from_yaml(install_md_dir / "manifest.yml")
    except Exception as e:
        # TODO: log
        # TODO: custom exception, direct user to inspect log
        print(f"[bold red]invalid registry entry for package '{name.name}'")
        raise e
    hooks = load_pkg_hooks(name.name, install_md_dir / "hooks.py")(info, manifest)
    try:
        hooks.prerm()
    except Exception as e:
        # TODO log
        print(f"[bold yellow]hook 'prerm' raised unhandled error")

    with db.get_conn() as conn:
        with ReversibleFileActions() as fs:
            installed_files  = db.pkg_files_installed(conn, name.name)
            for fpath, hash in installed_files:
                # TODO - should we bubble up with an iterator here or do it up-front like for install?
                fs.rm(fpath)
            fs.rmtree(install_md_dir)

        pkg_dirs = db.pkg_dirs(conn, name.name)
        db.rm_pkg(conn, name.name)
        for fpath, count in pkg_dirs:
            if count > 1:
                continue
            try:
                fpath.rmdir()
            except OSError as e:
                if e.errno == 39:  # Directory must be empty, this is OK
                    continue
                # TODO: log other rm error here.

    try:
        hooks.postrm()
    except Exception as e:
        # TODO log
        print("[bold yellow]hook 'postrm' raised unhandled error")
