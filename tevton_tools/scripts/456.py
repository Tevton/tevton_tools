import hou
import os
from pathlib import Path
import pipeline.projects_store as projects_store  # type: ignore


def get_projects_data_path():
    """
    If the user launches Houdini for the first time after installing the tools,
    the necessary folders are created.
    Return the path to Projects Data json file
    """
    folders = ["config", "logs", "cache", "temp"]
    if os.name == "nt":
        tool_dir = Path(os.getenv("APPDATA")) / "TVT"
    else:
        tool_dir = Path.home() / ".tvt"

    for folder in folders:
        (tool_dir / folder).mkdir(parents=True, exist_ok=True)

    projects_data = tool_dir / "projects_data.json"

    if not projects_data.exists():
        projects_data.write_text("{}", encoding="utf-8")

    return projects_data


get_projects_data_path()  # Ensure dirs and JSON file exist on first launch

try:
    # Check if project in the json file
    project_name = hou.hipFile.path().split("/")[-4]
    project = projects_store.get_project(project_name)

    path = Path(project["JOB"])
    project["PROJECT_FOLDERS"] = [f.name for f in path.iterdir() if f.is_dir()]
    projects_store.update_project(project_name, project)

    # Put env variables for the current houdini session
    for var, value in project.items():
        if var in ("PROJECT_NAME", "JOB", "PROJECT_OCIO"):
            hou.putenv(var, value)
except:
    pass
