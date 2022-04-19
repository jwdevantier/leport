import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple
from pypika import Query, Column, Table
from leport.impl.config import get_config
from leport.impl.types.pkg import PkgManifest

# TODO: some sort of migration log to permit future changes to schema?


T_SQLITE_MASTER = Table("sqlite_master")
T_PKGS = Table("pkgs")
T_FILES = Table("files")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_config().db_fpath)
    # disable python lib handling of transactions
    # unless we explicitly .execute("begin"), followed by "commit", "rollback", things are auto-committed
    conn.isolation_level = None
    return conn


def q_table_exists(tblname: str) -> str:
    return str(Query.from_("sqlite_master")
               .select("name")
               .where(T_SQLITE_MASTER.type == "table")
               .where(T_SQLITE_MASTER.name == tblname))


def table_exists(c: sqlite3.Connection, tblname: str) -> bool:
    return c.execute(q_table_exists(tblname)).fetchone() is not None


def q_create_pkg_table() -> str:
    return str(Query.create_table("pkgs").columns(
        Column("pkg", "VARCHAR(100)", nullable=False),
        Column("version", "VARCHAR(100)", nullable=False),
        Column("repo", "VARCHAR(100)", nullable=False),
        Column("info", "TEXT", nullable=False),
    ).primary_key("pkg").if_not_exists())


def q_create_files_table() -> str:
    return " ".join([
        "CREATE TABLE IF NOT EXISTS files (",
        "fpath VARCHAR(4096) PRIMARY KEY ON CONFLICT REPLACE NOT NULL,",
        "pkg VARCHAR(100) NOT NULL,",
        "sha256 VARCHAR(64) NOT NULL",
        ")"
    ])


def q_create_index(tbl: str, columns: List[str]) -> str:
    return f"""CREATE INDEX if not exists index_{tbl}_{"_".join(columns)} ON {tbl}({", ".join(columns)})"""


def q_drop_table(tbl: str, if_exists=True) -> str:
    return f"""DROP TABLE {"IF EXISTS" if if_exists else ""} {tbl}"""


def q_record_files(pkg: str, manifest: PkgManifest) -> str:
    q = Query.into(T_FILES)
    for fpath, sha256 in manifest.files.items():
        q = q.insert(str(fpath), pkg, sha256)
    return q.get_sql()


def which_pkg_owns_file(conn: sqlite3.Connection, fpath: str) -> Optional[str]:
    res = conn.execute(Query.from_(T_FILES).select("pkg").where(T_FILES.fpath == fpath).get_sql()).fetchone()
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


def init_db():
    with get_conn() as conn:
        conn.execute(q_create_pkg_table())
        conn.execute(q_create_files_table())
        conn.execute(q_create_index("files", ["pkg"]))

# Query: package files // dpkg -L <pkg>
# Query: packages // dpkg -l
# Query: package info // apt-cache show <pkg>  (if installed)
# Query: which package owns file? // dpkg -S
