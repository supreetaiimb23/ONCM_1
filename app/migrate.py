"""Database schema migrations (Alembic, files in migrations/), run on every start.

New database      -> `alembic upgrade head` creates everything.
Pre-Alembic DB    -> created by Base.metadata.create_all or supabase/schema.sql before migrations existed: the old
                     forward-only fixes run once (missing columns, uq_doc_version), the database is stamped at the
                     baseline revision, then later migrations apply as usual.
Changing a model  -> `.venv/bin/alembic revision --autogenerate -m "..."`, review the file, commit it with the change.
"""
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.core import engine
from app.models import Base

log = logging.getLogger("oncm")
ROOT = Path(__file__).resolve().parent.parent
BASELINE = "0001"


def _config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def add_missing_columns():
    """Pre-Alembic upgrade path: add model columns an existing table does not have yet."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl_type = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}'))
                log.warning("Added column %s.%s", table.name, col.name)


def ensure_unique_versions():
    """Pre-Alembic upgrade path: uq_doc_version. If concurrent uploads already gave two files the same version
    number the index cannot be built: start anyway and name them."""
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_version ON document_versions (document_id, version)"))
    except Exception:
        with engine.connect() as conn:
            dups = conn.execute(text("SELECT document_id, version FROM document_versions "
                                     "GROUP BY document_id, version HAVING COUNT(*) > 1")).all()
        log.error("uq_doc_version not created: duplicate version numbers (document_id, version) %s. "
                  "Renumber them, then restart.", [tuple(r) for r in dups])


def migrate():
    cfg = _config()
    with engine.connect() as conn:
        if conn.dialect.name == "postgresql":  # two app instances starting together must not migrate twice
            conn.execute(text("SELECT pg_advisory_lock(hashtext('oncm-migrations'))"))
            conn.commit()
        try:
            insp = inspect(conn)
            if insp.has_table("profiles") and not insp.has_table("alembic_version"):
                log.warning("Database predates migrations: bringing it to the baseline and stamping it")
                add_missing_columns()
                ensure_unique_versions()
                command.stamp(cfg, BASELINE)
            command.upgrade(cfg, "head")
        finally:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_unlock(hashtext('oncm-migrations'))"))
                conn.commit()
