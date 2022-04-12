from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union, List, Dict, Any
from pydantic import BaseModel, Field, Extra, validator
from pydantic import HttpUrl
import yaml
from leport.impl.types.validators import GitDirectoryPath, FilenamePath, PkgDirPath, PkgPath
from leport.impl.types.repos import Repo


class InvalidPackageNameError(Exception):
    def __init__(self, pkgname: str):
        self.pkgname = pkgname
        super().__init__(f"invalid package name '{pkgname}', name must of format [<repo>/]<pkg-name>")


@dataclass
class PkgSearchMatch:
    name: str
    repo: Repo
    dist: int


@dataclass
class PkgName:
    name: str
    repo: Optional[str] = None

    @staticmethod
    def from_str(pkgname: str) -> "PkgName":
        pkg_path = pkgname.split("/")
        if len(pkg_path) not in (1, 2):
            raise InvalidPackageNameError(pkgname)
        if len(pkg_path) == 2:
            return PkgName(repo=pkg_path[0], name=pkg_path[1])
        else:
            return PkgName(name=pkgname)


class PkgGitSource(BaseModel):
    git: Union[HttpUrl, GitDirectoryPath] = Field(description="url/path to git repository")
    branch: Optional[str] = Field(default=None, description="branch to use (default: 'master', iff. tag is unset)")
    tag: Optional[str] = Field(default=None, description="tag to use")
    name: str = Field(description="name of local directory")

    class Config:
        extra = Extra.forbid


class PkgHttpSource(BaseModel):
    uri: HttpUrl = Field(description="uri to resource")
    sha256: Optional[str] = Field(default=None, description="checksum of provided file")

    class Config:
        extra = Extra.forbid


class PkgFileSource(BaseModel):
    filename: FilenamePath = Field(description="path to file, should be local to pkg dir")
    sha256: Optional[str] = Field(default=None, description="checksum of provided file")

    class Config:
        extra = Extra.forbid


PkgSource = Union[PkgGitSource, PkgHttpSource, PkgFileSource]


class PkgInfo(BaseModel):
    name: str
    # version string can be dynamically set, but MUST be set before `depends` build stage
    version: Optional[str] = Field(default=None, description="version string (if fixed)")
    release: int
    description: str = Field(default="<no description>")
    sources: List[PkgSource]
    url: Optional[HttpUrl]

    @staticmethod
    def from_yaml(yml: Union[Path, str]) -> "PkgInfo":
        # TODO: handle errors (missing, invalid format, failed validation)
        txt = yml.read_text() if isinstance(yml, Path) else yml
        return PkgInfo(**yaml.load(txt, Loader=yaml.Loader))


class PkgManifestStat(BaseModel):
    user: str
    group: str
    mode: str

    @validator("mode")
    def valid_mode(cls, v):
        if not isinstance(v, str):
            raise ValueError(f"expected mode expressed as a string value")
        v = v.strip()
        if not len(v) == 3:
            raise ValueError(f"expected a 3-digit octal value")

        for ch in v:
            if ch not in {"0", "1", "2", "4", "7", "6", "5", "4", "3"}:
                raise ValueError("not a valid octal mode value")
        return v


class PkgManifest(BaseModel):
    """Represents the package manifest in full."""
    # (file) Path -> Sha256
    file_checksums: Dict[Path, str]
    # (file|dir) Path -> ownership information
    stat: Dict[Path, PkgManifestStat]

    def serialize(self) -> Dict[str, Any]:
        return {
            "file_checksums": {str(k): v for k, v in self.file_checksums.items()},
            "stat": {str(k): stat.dict() for k, stat in self.stat.items()}
        }

    def to_yaml(self, stream):
        yaml.dump(self.serialize(), stream)

    @staticmethod
    def from_yaml(yml: Union[str, Path]) -> "PkgManifest":
        txt = yml.read_text() if isinstance(yml, Path) else yml
        return PkgManifest(**yaml.load(txt, Loader=yaml.Loader))


class PkgDir(BaseModel):
    name: str
    repo: "Repo"
    path: PkgDirPath

    @property
    def build_py(self) -> Path:
        """Return path to package build file"""
        return self.path / "build.py"

    @property
    def info_yml(self) -> Path:
        """Return path to package info file"""
        return self.path / "info.yml"

    @property
    def hooks_py(self) -> Optional[Path]:
        """Return path to hooks file if it exists."""
        hp = self.path / "hooks.py"
        if hp.exists():
            return hp
        return None


class PkgFile(BaseModel):
    path: PkgPath


class PkgBuildSteps(ABC):
    def __init__(self, info: PkgInfo, build_dir: Path, dest_dir: Path):
        self.__info = info
        self.__build_dir = build_dir
        self.__dest_dir = dest_dir
        self.__perms = {}
        self.on_init()

    def on_init(self) -> None:
        pass

    @property
    def info(self) -> PkgInfo:
        return self.__info

    @property
    def build_dir(self) -> Path:
        return self.__build_dir

    @property
    def dest_dir(self) -> Path:
        return self.__dest_dir

    @property
    def stat(self) -> Dict[Path, Dict[str, str]]:
        return {**self.__perms}

    def set_stat(self, path: Path,
                  user: Optional[str],
                  group: Optional[str],
                  mode: Optional[str]):

        if path not in self.__perms:
            opts = {}
            self.__perms[path] = opts
        else:
            opts = self.__perms[path]

        if path.is_absolute():
            raise RuntimeError("perms cannot be set on absolute paths, must be set relative to the dest_dir")
        if not (self.dest_dir / path).exists():
            raise RuntimeError("error defining perms for '{path}' in dest_dir, file/directory does not exist")

        if user:
            opts["user"] = user
        if group:
            opts["group"] = group
        if mode:
            opts["mode"] = mode

    @abstractmethod
    def prepare(self, build_dir: Path):
        """Extract sources from `src_dir` to `build_dir`, apply patches etc.

        Args:
            build_dir: directory in which the package should be extracted and built

        Returns:
            None
        """
        pass

    def pkg_version(self, build_dir: Path) -> Optional[str]:
        """Dynamically determine the package version (Optional)

        Some packages may have their version dynamically determined.

        Args:
            build_dir: directory in which the package should be extracted and built

        Returns:
            A dynamically determined version string, or None, indicating
            that the version is given by the package's `info.yml` file.
        """
        return None

    @abstractmethod
    def depends(self, build_dir: Path):
        """Run checks to determine if package requirements are met

        Should ideally avoid relying on OS package managers to determine if
        needs are met, but attempt to locate binaries and libraries directly.

        Args:
            build_dir: directory in which the package should be extracted and built

        Returns:
            None
        """
        pass

    @abstractmethod
    def build(self, build_dir: Path, dest_dir: Path) -> None:
        """

        Args:
            build_dir: directory in which the package should be extracted and built
            dest_dir: fake root into which the package should be installed

        Returns:
            None
        """
        pass

    @abstractmethod
    def check(self, build_dir: Path, dest_dir: Path):
        """Run any self-checks or tests to determine that the package works.

        Args:
            build_dir: directory in which the package should be extracted and built
            dest_dir: fake root into which the package should be installed

        Returns:
            None
        """
        pass

    @abstractmethod
    def install(self, build_dir: Path, dest_dir: Path):
        """Install the software to `dest_dir` and do any last pre-packaging changes.

        Args:
            build_dir: directory in which the package should be extracted and built
            dest_dir: fake root into which the package should be installed

        Returns:
            None
        """
        pass


class PkgHooks(object):
    def __init__(self, info: PkgInfo, manifest: PkgManifest):
        self.info = info
        self.manifest = manifest

    def preinst(self):
        """run before installation of package"""
        pass

    def postinst(self):
        """run after package has been installed"""
        pass

    def prerm(self):
        """run before package is removed"""
        pass

    def postrm(self):
        """run after package is removed"""
        pass
