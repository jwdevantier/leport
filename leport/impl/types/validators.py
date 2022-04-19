import tarfile
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Any
from pydantic.validators import path_validator, path_exists_validator
from pydantic.errors import PathNotADirectoryError, _PathValueError
from leport.utils.fileutils import user_home

if TYPE_CHECKING:
    from pydantic.typing import CallableGenerator


class PathNotAGitDirectoryError(_PathValueError):
    code = 'path.not_a_git_directory'
    msg_template = 'path "{path}" does not point to a git directory'


class PathNotATarfileError(_PathValueError):
    code = 'path.not_a_tarfile'
    msg_template = 'path "{path}" does not point to a tarfile'


class PkgPath(Path):
    @classmethod
    def __modify_schema__(cls, field_schema: Dict[str, Any]) -> None:
        field_schema.update(format='directory-path')

    @classmethod
    def __get_validators__(cls) -> 'CallableGenerator':
        yield path_validator
        yield path_exists_validator
        yield cls.validate

    @classmethod
    def validate(cls, value: Path) -> Path:
        if not tarfile.is_tarfile(value):
            raise PathNotATarfileError(path=value)
        with tarfile.open(value, "r:xz") as fh:
            missing = {"info.yml", "manifest.yml"} - set(fh.getnames())
            if missing:
                raise ValueError(f"pkg missing required metadata files: {', '.join(missing)}")

        return value


class GitDirectoryPath(Path):
    @classmethod
    def __modify_schema__(cls, field_schema: Dict[str, Any]) -> None:
        field_schema.update(format='directory-path')

    @classmethod
    def __get_validators__(cls) -> 'CallableGenerator':
        yield path_validator
        yield path_exists_validator
        yield cls.validate

    @classmethod
    def validate(cls, value: Path) -> Path:
        if not value.is_dir():
            raise PathNotADirectoryError(path=value)

        return value


ConcretePath = type(Path.home())


class DirOrMissing(ConcretePath):
    @classmethod
    def __get_validators__(cls) -> 'CallableGenerator':
        yield path_validator
        yield cls.validate

    @classmethod
    def validate(cls, value: Path) -> Path:
        svalue = str(value)
        if svalue.startswith("~"):
            value = user_home() / str(value)[1 if len(svalue) > 1 and svalue[1] != "/" else 2:]
        if value.exists() and not value.is_dir():
            raise ValueError("exists, but is not a directory")
        return value


class FilenamePath(ConcretePath):
    @classmethod
    def __get_validators__(cls) -> 'CallableGenerator':
        yield path_validator
        yield cls.validate

    @classmethod
    def validate(cls, value: Path) -> Path:
        if value.name != str(value):
            raise ValueError("source file cannot be a path, must be a plain filename")
        return value


class PkgDirPath(ConcretePath):
    @classmethod
    def __get_validators__(cls) -> 'CallableGenerator':
        yield path_validator
        yield cls.validate

    @classmethod
    def validate(cls, value: Path) -> Path:
        if not value.exists():
            raise ValueError("pkg directory does not exist")

        if not value.is_dir():
            raise ValueError("not a directory")

        info_yml = value / "info.yml"
        build_py = value / "build.py"
        hooks_py = value / "hooks.py"

        if not info_yml.exists():
            raise ValueError("missing info.yml metadata file")
        elif not info_yml.is_file():
            raise ValueError("info.yml in package directory is not a file")
        elif not build_py.exists():
            raise ValueError("missing require build.py file containing pkg build instructions")
        elif not build_py.is_file():
            raise ValueError("build.py in package directory is not a file")
        elif hooks_py.exists() and not hooks_py.is_file():
            raise ValueError("hook.py exists, but is not a file")

        return value
