from pathlib import Path
import json
from config.config import PROJECTS_JSON_PATH


class FTPConfigError(Exception):
    """FTP configuration error — invalid settings or missing project."""

    pass


# -----------------------------------
# Project settings
# -----------------------------------


def get_ftp_settings(project_name: str) -> dict:
    """
    Return FTP settings for a project from projects_data.json.

    Returns:
        dict with keys: host, user, password, port

    Raises:
        FTPConfigError: if file not found, JSON is corrupt, or project is missing
    """
    data = _load_projects_json()

    if project_name not in data:
        raise FTPConfigError(f"Project '{project_name}' not found in config")

    project = data[project_name]

    return {
        "host": project.get("PROJECT_FTP_HOST", ""),
        "user": project.get("PROJECT_FTP_USER", ""),
        "password": project.get("PROJECT_FTP_PASSWORD", ""),
        "port": _parse_port(project.get("PROJECT_FTP_PORT"), project_name),
    }


def _load_projects_json() -> dict:
    """Load and return contents of projects_data.json."""
    path = Path(PROJECTS_JSON_PATH)

    if not path.exists():
        raise FTPConfigError(f"Projects JSON not found: {PROJECTS_JSON_PATH}")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FTPConfigError(f"Invalid JSON in projects file: {e}")


def _parse_port(raw, project_name: str = "") -> int:
    """Safely convert port value to int. Defaults to 21."""
    if raw is None:
        return 21
    try:
        port = int(raw)
        if port < 1 or port > 65535:
            raise ValueError
        return port
    except (ValueError, TypeError):
        print(
            f"Warning: invalid FTP port '{raw}' for '{project_name}', defaulting to 21"
        )
        return 21


# -----------------------------------
# Formatters
# -----------------------------------

_SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"]


def format_size(size: int) -> str:
    """Format file size into a human-readable string."""
    if size is None or size < 0:
        return "0 B"

    for unit in _SIZE_UNITS[:-1]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024

    return f"{size:.1f} {_SIZE_UNITS[-1]}"
