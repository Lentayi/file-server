import os
from pathlib import Path


DEFAULT_STORAGE_QUOTA = 10 * 1024 * 1024 * 1024
DEFAULT_UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
DEFAULT_UPLOAD_RAM_BUFFER = 0
SIZE_UNITS = {
    "B": 1,
    "KB": 1024,
    "K": 1024,
    "MB": 1024 ** 2,
    "M": 1024 ** 2,
    "GB": 1024 ** 3,
    "G": 1024 ** 3,
    "TB": 1024 ** 4,
    "T": 1024 ** 4,
}


def load_dotenv(dotenv_path):
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_size(raw_value, default):
    value = str(raw_value or "").strip().upper().replace(" ", "")
    if not value:
        return default

    number_part = ""
    unit_part = ""
    for char in value:
        if char.isdigit() or char == ".":
            number_part += char
        else:
            unit_part += char

    try:
        number = float(number_part)
    except ValueError:
        return default

    multiplier = SIZE_UNITS.get(unit_part or "B")
    if not multiplier:
        return default

    return int(number * multiplier)


def resolve_storage_path(raw_path, base_dir):
    if not raw_path:
        return base_dir / "uploads"

    storage_path = Path(raw_path).expanduser()
    if not storage_path.is_absolute():
        storage_path = base_dir / storage_path
    return storage_path


def load_app_config(base_dir):
    base_dir = Path(base_dir).resolve()
    load_dotenv(base_dir / ".env")

    storage_path = resolve_storage_path(os.environ.get("FILE_SERVER_STORAGE_PATH"), base_dir)
    storage_quota = parse_size(os.environ.get("FILE_SERVER_STORAGE_QUOTA"), DEFAULT_STORAGE_QUOTA)
    upload_chunk_size = parse_size(
        os.environ.get("FILE_SERVER_UPLOAD_CHUNK_SIZE"),
        DEFAULT_UPLOAD_CHUNK_SIZE,
    )
    upload_ram_buffer = parse_size(
        os.environ.get("FILE_SERVER_UPLOAD_RAM_BUFFER"),
        DEFAULT_UPLOAD_RAM_BUFFER,
    )

    return {
        "SECRET_KEY": os.environ.get("FILE_SERVER_SECRET_KEY", "supersecret"),
        "SQLALCHEMY_DATABASE_URI": os.environ.get("FILE_SERVER_DATABASE_URI", "sqlite:///db.sqlite"),
        "UPLOAD_FOLDER": str(storage_path),
        "MAX_CONTENT_LENGTH": storage_quota,
        "UPLOAD_STORAGE_LIMIT": storage_quota,
        "UPLOAD_CHUNK_SIZE": upload_chunk_size,
        "UPLOAD_RAM_BUFFER_LIMIT": upload_ram_buffer,
    }
