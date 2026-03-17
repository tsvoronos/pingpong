import base64
from logging.config import fileConfig
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

from alembic import context
from pingpong.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def _sanitize_db_suffix(raw: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_")
    if not cleaned:
        cleaned = "branch"
    if cleaned[0].isdigit():
        cleaned = f"b_{cleaned}"
    cleaned = cleaned[:40].rstrip("_")
    return cleaned or "branch"


def _is_development_config() -> bool:
    raw_config = os.environ.get("CONFIG")
    if raw_config:
        try:
            config_data = tomllib.loads(base64.b64decode(raw_config).decode("utf-8"))
        except Exception:
            return False
    else:
        config_path = Path(os.environ.get("CONFIG_PATH", "config.toml"))
        try:
            config_data = tomllib.loads(config_path.read_text())
        except Exception:
            return False

    if not isinstance(config_data, dict):
        return False
    return bool(config_data.get("development", False))


def _resolve_worktree_branch_name() -> str | None:
    try:
        git_common_dir = Path(
            subprocess.check_output(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                text=True,
            ).strip()
        )
        repo_root = Path(
            subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                text=True,
            ).strip()
        )
    except Exception:
        return None

    main_repo_root = git_common_dir.parent
    worktree_root = main_repo_root.parent / f"{main_repo_root.name}-worktrees"
    if worktree_root not in repo_root.parents:
        return None

    ports_file = worktree_root / ".worktree-ports.json"
    worktree_name = repo_root.name

    if ports_file.is_file():
        try:
            ports_data = json.loads(ports_file.read_text())
            branch_name = ports_data.get(worktree_name, {}).get("branch")
            if isinstance(branch_name, str) and branch_name:
                return branch_name
        except Exception:
            pass
    return None


def _configure_worktree_database_url() -> None:
    # Worktree-specific databases are local development infrastructure only.
    if not _is_development_config():
        return

    branch_name = _resolve_worktree_branch_name()
    if not branch_name:
        return

    url = make_url(config.get_main_option("sqlalchemy.url"))
    if url.drivername.startswith("postgresql"):
        db_name = f"pingpong_{_sanitize_db_suffix(branch_name)}"
        config.set_main_option(
            "sqlalchemy.url",
            str(url.set(database=db_name).render_as_string(hide_password=False)),
        )


_configure_worktree_database_url()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
