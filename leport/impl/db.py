import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple
from leport.impl.config import get_config
from leport.impl.types.pkg import PkgManifest, PkgInfo

# TODO: some sort of migration log to permit future changes to schema?


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_config().db_fpath)
    # disable python lib handling of transactions
    # unless we explicitly .execute("begin"), followed by "commit", "rollback", things are auto-committed
    conn.isolation_level = None
    return conn


def table_exists(c: sqlite3.Connection, tblname: str) -> bool:
    return c.execute(" ".join([
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?"
    ]), (tblname,)).fetchone()


def q_create_pkgs_table() -> str:
    return " ".join([
        "CREATE TABLE IF NOT EXISTS pkgs (",
        "pkg VARCHAR(100) PRIMARY KEY NOT NULL,",
        "version VARCHAR(100) NOT NULL,",
        "release INTEGER NOT NULL",
        ")"
    ])


def q_create_files_table() -> str:
    return " ".join([
        "CREATE TABLE IF NOT EXISTS files (",
        "fpath VARCHAR(4096) PRIMARY KEY ON CONFLICT REPLACE NOT NULL,",
        "pkg VARCHAR(100) NOT NULL,",
        "sha256 VARCHAR(64) NOT NULL",
        ")"
    ])


def q_create_dirs_table() -> str:
    return " ".join([
        "CREATE TABLE IF NOT EXISTS dirs (",
        "dir TEXT NOT NULL,",
        "pkg TEXT NOT NULL,",
        "PRIMARY KEY (dir, pkg)",
        ")"
    ])


def q_create_index(tbl: str, columns: List[str]) -> str:
    return f"""CREATE INDEX if not exists index_{tbl}_{"_".join(columns)} ON {tbl}({", ".join(columns)})"""


def q_drop_table(tbl: str, if_exists=True) -> str:
    return f"""DROP TABLE {"IF EXISTS" if if_exists else ""} {tbl}"""


def record_pkg(conn: sqlite3.Connection, info: PkgInfo):
    conn.execute(
        """INSERT INTO pkgs (pkg, version, release) VALUES (?, ?, ?)""",
        (info.name, info.version, info.release))


def has_pkg(conn: sqlite3.Connection, pkg_name: str) -> bool:
    return conn.execute(
        """SELECT pkg FROM pkgs WHERE pkg = ?""", (pkg_name,)).fetchone() is not None


def record_files(conn: sqlite3.Connection, pkg: str, manifest: PkgManifest) -> None:
    conn.executemany(
        "INSERT INTO files (fpath, pkg, sha256) VALUES (?, ?, ?)",
        ((str(fpath), pkg, sha256) for fpath, sha256 in manifest.files.items())
    )


def which_pkg_owns_file(conn: sqlite3.Connection, fpath: str) -> Optional[str]:
    res = conn.execute("SELECT pkg FROM files WHERE fpath = ?", (fpath,)).fetchone()
    if res is None:
        return None
    return res[0]


def pkg_files_installed(conn: sqlite3.Connection, pkg: str) -> List[Tuple[Path, str]]:
    return [
        (Path(fpath), hash)
        for fpath, hash in conn.execute("SELECT fpath, sha256 FROM files WHERE pkg = ?", (pkg,)).fetchall()]


def rm_pkg(conn: sqlite3.Connection, pkg: str):
    conn.execute("DELETE FROM files where pkg = ?", (pkg,))
    conn.execute("DELETE FROM pkgs where pkg = ?", (pkg,))


def q_pkgs_ls():
    return "SELECT pkg, version, release FROM pkgs"


def init_db():
    with get_conn() as conn:
        conn.execute(q_create_pkgs_table())
        conn.execute(q_create_files_table())
        conn.execute(q_create_index("files", ["pkg"]))

# Query: package files // dpkg -L <pkg>
# Query: packages // dpkg -l
# Query: package info // apt-cache show <pkg>  (if installed)
# Query: which package owns file? // dpkg -S
