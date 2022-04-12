"""
PKG Build Module

This module handles the overall process of building a package, divided into the
following distinct steps, also corresponding to hook functions which a package's
build script must implement:

(Prepare)
    - Create build directory
    - Fetch sources (if missing/hash changed)
    - Verify checksums of sources
Prepare
    - extract software, apply patches
Version (Optional)
    - (If pkg versioning is dynamic) call hook to determine pkg's version
Depends
    - Do checks to determine if system has required supporting binaries, libraries and resources
Configure
    - Configure software
Build
    - Execute relevant commands to compile the software
Check
    - Run tests to determine package functions as intended
Install
    - Install package to destination directory and do last-minute tweaks before the result is packaged
(Package)
    - create the finished pkg file
"""
from urllib.parse import urlparse
from pathlib import Path
import shutil
import json
import tarfile
from typing import Dict
from rich import print
from rich.progress import Progress
import yaml
from leport.impl.types.pkg import PkgDir, PkgInfo, PkgFileSource, PkgHttpSource, PkgGitSource, PkgBuildSteps, PkgManifest, PkgManifestStat
from leport.impl.config import get_config, Config
from leport.utils.fileutils import sha256sum, fetch_with_progress, load_module_from_path, cwd, walk
import leport.utils.git as lgit


def prepare_step(cfg: Config, pkgdir: PkgDir, info: PkgInfo, build_dir: Path):
    build_dir.mkdir(parents=True, exist_ok=True)

    for source in info.sources:
        if isinstance(source, PkgGitSource):
            lgit.refresh_git(src=source.git,
                             branch=source.branch,
                             tag=source.tag,
                             dst=build_dir / source.name)
        elif isinstance(source, PkgHttpSource):
            fname = Path(urlparse(source.uri).path).name
            dst = build_dir / fname
            download = False
            file_checksum = None

            if dst.exists() and source.sha256:
                file_checksum = sha256sum(dst)
                if file_checksum != source.sha256:
                    print("[yellow] file sha256 checksum does not match expected, re-downloading file")
                    dst.unlink()
                    download = True
            elif not dst.exists():
                download = True

            if download:
                file_checksum = None
                # TODO: handle errors from 404, 500, etc..
                fetch_with_progress(source.uri, dst, f"Downloading source {fname}...")

            if source.sha256:
                file_checksum = file_checksum or sha256sum(dst)
                if source.sha256 != file_checksum:
                    # TODO: better error-reporting
                    raise RuntimeError("Downloaded file checksum differs from source checksum in info file, abort!")
        elif isinstance(source, PkgFileSource):
            fsrc = pkgdir.path / source.filename.name
            fdst = build_dir / source.filename.name

            if fdst.exists():
                if source.sha256 is None or source.sha256 == sha256sum(fdst):
                    continue
                fdst.unlink()

            if not fsrc.exists():
                # TODO: better error-reporting
                raise RuntimeError(f"file {source.filename} not found in package directory")
            if source.sha256 and source.sha256 != sha256sum(fsrc):
                # TODO: better error-reporting
                raise RuntimeError(f"checksum for file differs from the one given in the package info file")

            shutil.copy2(fsrc, fdst, follow_symlinks=False)
        else:
            raise ValueError(f"No idea how prepare with `{type(source)}`.")
    pass


def makepkg(info: PkgInfo,
            pkg_dir: PkgDir,
            pkg_root: Path,
            perms: Dict[Path, Dict[str, str]],
            dest: Path):
    with Progress() as progress:
        with tarfile.open(dest, "w:xz") as tar:
            # ensure metadata files do not exist in package root while we compute the package manifest
            (pkg_root / "info.yml").unlink(missing_ok=True)
            (pkg_root / "manifest.yml").unlink(missing_ok=True)

            # add files & directories to the package
            # (progress bar is a bit rough, but adding each file individually doubles package time)
            tar_task = progress.add_task(
                "archiving package contents...",
                total=len(list(pkg_root.iterdir())))

            for file in pkg_root.iterdir():
                tar.add(file, arcname=Path("files") / file.name)
                progress.update(tar_task, advance=1)

            if pkg_dir.hooks_py:
                tar.add(pkg_dir.hooks_py, arcname="hooks.py")

            # write and add info.yml metadata file
            with open(pkg_root / "info.yml", "w") as fh:
                # TODO: there has got to be a better way to reliably serialize all parts of the model
                yaml.dump(json.loads(info.json()), fh)
            tar.add(pkg_root / "info.yml", arcname="info.yml")

            # compute manifest by walking through
            checksums = {}
            checksum_task = progress.add_task(
                "calculating package file checksums...",
                total=len(list(walk(pkg_root)))
            )

            root_path = Path("/")
            for file in walk(pkg_root):
                checksums[str(root_path / file)] = sha256sum(pkg_root / file)
                progress.update(checksum_task, advance=1)

            # determine set of all files & dirs part of this package
            pkg_contents = set(walk(pkg_root, include_dirs=True))
            pkg_contents.remove(Path("info.yml"))

            # ... and compare it to the set of all files/dirs for which the pkg defined permissions
            pkg_perms = set(perms.keys())
            if (pkg_contents - pkg_perms):
                print("UN-PERMED FILES")
                print(pkg_contents - pkg_perms)
                # TODO: some files/dirs have no perms assigned, raise error
                import sys
                sys.exit(1)
            else:
                print("ALL PERMED")

            # TODO: can raise errors if entry does not have all required fields set.
            print(repr(perms))
            manifest = PkgManifest(
                file_checksums=checksums,
                stat={root_path / k: PkgManifestStat(**v)
                       for k, v in perms.items()}
            )
            with open(pkg_root / "manifest.yml", "w") as fh:
                manifest.to_yaml(fh)
            tar.add(pkg_root / "manifest.yml", arcname="manifest.yml")


def build(pkg_dir: PkgDir, clean: bool = True):
    info = PkgInfo.from_yaml(pkg_dir.info_yml)
    cfg = get_config()
    build_dir: Path = cfg.build / pkg_dir.repo.name / pkg_dir.name
    dest_dir = cfg.data / "destdir" / pkg_dir.repo.name / pkg_dir.name

    if clean and build_dir.exists():
        shutil.rmtree(build_dir)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True)

    print("[bold][cyan]:: [/cyan]Creating directories, acquiring sources...")
    prepare_step(cfg, pkg_dir, info, build_dir)
    # TODO: escape module names better

    print("[bold][cyan]:: [/cyan]Loading build script...")
    build_module = load_module_from_path(
        pkg_dir.build_py,
        f"""{pkg_dir.repo.name.replace(" ", "_")}.{pkg_dir.name.replace(" ", "_")}""")

    # TODO: raise more informative error
    if (not hasattr(build_module, "Build")
        or not issubclass(build_module.Build, PkgBuildSteps)):
        raise ValueError("invalid build module - must export `Build` type of type PkgBuildSteps")
    build: PkgBuildSteps = build_module.Build(info, build_dir, dest_dir)

    # # delegate to prepare
    print("[bold][cyan]:: [/cyan]`prepare` step")
    with cwd(build_dir):
        build.prepare(info, build_dir)

    print("[bold][cyan]:: [/cyan]`prepare` step")
    # update with dynamically inferred version (if defined)
    with cwd(build_dir):
        version = build.pkg_version(build_dir)
    if version:
        info.version = version

    print("[bold][cyan]:: [/cyan]Determine if OS has requisite dependencies...")
    with cwd(build_dir):
        build.depends(build_dir)

    print("[bold][cyan]:: [/cyan]Build package...")
    with cwd(build_dir):
        build.build(build_dir, dest_dir)

    print("[bold][cyan]:: [/cyan]Run package checks...")
    with cwd(build_dir):
        build.check(build_dir, dest_dir)

    print("[bold][cyan]:: [/cyan]Install files to package root...")
    with cwd(build_dir):
        build.install(build_dir, dest_dir)

    pkg_dest = cfg.pkgs / pkg_dir.repo.name / f"{pkg_dir.name}.xz"
    pkg_dest.unlink(missing_ok=True)
    pkg_dest.parent.mkdir(parents=True, exist_ok=True)
    print("[bold][cyan]:: [/cyan]Creating package file...")
    makepkg(info, pkg_dir, dest_dir, build.stat, pkg_dest)
    print(f"[bold][green]Success! Package stored at '{pkg_dest}'")


__all__ = ["build"]
