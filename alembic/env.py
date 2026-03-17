import base64
from logging.config import fileConfig
import os
from pathlib import Path
import tomllib

from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import URL

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


_DEV_CONFIG_FILENAMES = (
    "config.local.toml",
    "config.dev.toml",
    "config.toml",
)


def _load_config_data() -> dict[str, object] | None:
    config_path_env = os.environ.get("CONFIG_PATH")
    if config_path_env:
        config_data = tomllib.loads(Path(config_path_env).read_text())
    else:
        raw_config = os.environ.get("CONFIG")
        if raw_config:
            config_data = tomllib.loads(base64.b64decode(raw_config).decode("utf-8"))
        else:
            config_data = None
            for filename in _DEV_CONFIG_FILENAMES:
                config_path = Path(filename)
                if config_path.is_file():
                    config_data = tomllib.loads(config_path.read_text())
                    break

    if config_data is None:
        return None

    if not isinstance(config_data, dict):
        raise ValueError("Config root must be a TOML object")
    return config_data


def _is_development_config(config_data: dict[str, object]) -> bool:
    return bool(config_data.get("development", False))


def _resolve_database_url(config_data: dict[str, object]) -> str:
    raw_db_config = config_data.get("db")
    if not isinstance(raw_db_config, dict):
        raise ValueError("Config must define a [db] section")

    engine = raw_db_config.get("engine")

    if engine == "postgres":
        username = raw_db_config.get("user")
        password = raw_db_config.get("password")
        host = raw_db_config.get("host")
        database = raw_db_config.get("database")
        port = raw_db_config.get("port")
        sslmode = raw_db_config.get("sslmode")

        required_fields = {
            "db.user": username,
            "db.password": password,
            "db.host": host,
            "db.database": database,
        }
        missing_fields = [
            field_name
            for field_name, value in required_fields.items()
            if not isinstance(value, str) or not value
        ]
        if missing_fields:
            raise ValueError(
                f"Postgres config missing required string fields: {', '.join(missing_fields)}"
            )
        if port is not None and not isinstance(port, int):
            raise ValueError("db.port must be an integer when set")
        if sslmode is not None and not isinstance(sslmode, str):
            raise ValueError("db.sslmode must be a string when set")

        query: dict[str, str] = {}
        if sslmode:
            query["sslmode"] = sslmode

        return URL.create(
            "postgresql",
            username=username,
            password=password,
            host=host,
            port=port,
            database=database,
            query=query,
        ).render_as_string(hide_password=False)

    if engine == "sqlite":
        path = raw_db_config.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("SQLite config requires a non-empty db.path")
        return URL.create("sqlite", database=path).render_as_string(hide_password=False)

    raise ValueError(f"Unsupported db.engine: {engine!r}")


def _configure_database_url() -> None:
    # Only override alembic.ini when the active config is explicitly development.
    config_data = _load_config_data()
    if config_data is None or not _is_development_config(config_data):
        return

    database_url = _resolve_database_url(config_data)
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


_configure_database_url()


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
