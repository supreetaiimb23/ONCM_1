"""Alembic environment: uses the app's own engine (DATABASE_URL from .env) and models."""
from alembic import context

from app.core import engine
from app.models import Base

target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=str(engine.url), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=engine.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    with engine.connect() as connection:
        # SQLite cannot ALTER most things in place; batch mode copies the table instead
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                          render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
