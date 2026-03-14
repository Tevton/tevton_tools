import hou
from pathlib import Path
import tvt_utils

houdini_main_dir = hou.text.expandString("$HFS")

USER_DATA_PATH = tvt_utils.get_user_tool_dir()
PROJECTS_JSON_PATH = Path(USER_DATA_PATH) / "projects_data.json"
CUSTOM_OCIO_PATH = hou.text.expandString("$HOUDINI_USER_PREF_DIR/ocio")
FTP_SHOT_PATH = "/OUT/FX/{shot_name}"
FTP_SOURCE_PATH = "IN/{shot_name}"
HIP_EXTENSIONS = [".hip", ".hipnc", ".hiplc"]
