from pathlib import Path
import json
from config.config import PROJECTS_JSON_PATH, SHOT_STATUSES_JSON_PATH


class ProjectStoreError(Exception):
    pass


def load_projects() -> dict:
    """Load all projects from JSON file. Raises ProjectStoreError if file is missing or corrupted."""
    if not Path(PROJECTS_JSON_PATH).exists():
        raise ProjectStoreError(f"Projects file not found at {PROJECTS_JSON_PATH}")

    with open(PROJECTS_JSON_PATH, "r") as f:
        return json.load(f)


def save_projects(projects: dict):
    """Write projects dict to JSON file. Raises ProjectStoreError on write failure."""
    try:
        with open(PROJECTS_JSON_PATH, "w") as f:
            json.dump(projects, f, indent=4)
    except Exception as e:
        raise ProjectStoreError(f"Failed to save projects: {e}")


def get_project(project_name: str) -> dict:
    """Return project data by name. Raises ProjectStoreError if not found."""
    projects = load_projects()
    if project_name not in projects:
        raise ProjectStoreError(f"Project not found: {project_name}")

    return projects.get(project_name)


def add_project(project_name: str, project_data: dict):
    """Add a new project. Raises ProjectStoreError if project already exists."""
    projects = load_projects()
    if project_name in projects:
        raise ProjectStoreError(f"Project already exists: {project_name}")

    projects[project_name] = project_data
    save_projects(projects)


def remove_project(project_name: str):
    """Remove project by name. Raises ProjectStoreError if not found."""
    projects = load_projects()
    if project_name not in projects:
        raise ProjectStoreError(f"Project not found: {project_name}")

    del projects[project_name]
    save_projects(projects)


def update_project(project_name: str, project_data: dict):
    """Update existing project data. Raises ProjectStoreError if project not found."""
    projects = load_projects()
    if project_name not in projects:
        raise ProjectStoreError(f"Project not found: {project_name}")

    projects[project_name] = project_data
    save_projects(projects)


def project_exists(project_name: str) -> bool:
    """Check if a project exists by name."""
    projects = load_projects()
    return project_name in projects


def list_projects() -> list:
    """Return a list of all project names."""
    projects = load_projects()
    return list(projects.keys())


def upsert_project(project_name: str, project_data: dict):
    """Create or update project data regardless of whether it exists. Raises ProjectStoreError on save failure."""
    try:
        projects = load_projects()
    except ProjectStoreError:
        projects = {}
    projects[project_name] = project_data
    save_projects(projects)


def _load_shot_statuses() -> dict:
    if not Path(SHOT_STATUSES_JSON_PATH).exists():
        return {}
    try:
        with open(SHOT_STATUSES_JSON_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_shot_statuses(data: dict):
    try:
        with open(SHOT_STATUSES_JSON_PATH, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        raise ProjectStoreError(f"Failed to save shot statuses: {e}")


def get_all_shot_statuses(project_name: str) -> dict:
    """Return {shot_name: status} for a project. Single file read."""
    return _load_shot_statuses().get(project_name, {})


def set_shot_status(project_name: str, shot_name: str, status: str):
    data = _load_shot_statuses()
    data.setdefault(project_name, {})[shot_name] = status
    _save_shot_statuses(data)
