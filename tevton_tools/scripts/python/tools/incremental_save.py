def incremental_save():
    import hou

    filename = hou.hipFile.basename()
    flag = False

    for c in filename:
        if c.isdigit():
            flag = True
            break

    if flag == True:
        answer = hou.ui.displayMessage(
            f"Do you want to save {filename} with icremented version?",
            buttons=("Save", "Cancel"),
            close_choice=1,
            severity=hou.severityType.Message,
            title="Increment Save",
        )
        if answer == 0:
            try:
                hou.hipFile.saveAndIncrementFileName()

                new_filename = hou.hipFile.basename()

                hou.ui.displayMessage(
                    "File saved!\n" f"New name : {new_filename}",
                    severity=hou.severityType.Message,
                    title="Success",
                )
            except Exception as e:
                hou.ui.displayMessage(
                    "Failed to save current file!\n",
                    f"Error : {e}",
                    severity=hou.severityType.Fatal,
                    title="Error",
                )

    else:
        hou.ui.displayMessage(
            f"Current file {filename} doesn't have numbers!\n"
            "Please save current file with version definition",
            severity=hou.severityType.Error,
            title="Error",
        )
