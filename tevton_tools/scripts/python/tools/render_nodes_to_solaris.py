def translate_nodes_to_solaris():
    import hou
    
    
    def create_sop_import_node(node, reference_node_pos, offset, flag):
        # Checking for already created nodes
        sop_import_node_name = node.name()
        if sop_import_node_name.upper().startswith(
            "RENDER_"
        ) or sop_import_node_name.upper().startswith("OUT_"):
            sop_import_node_name = sop_import_node_name[
                sop_import_node_name.find("_") + 1 :
            ]

        # Check if a node with the same name already exists in /stage
        if hou.node("/stage/" + sop_import_node_name.upper()) is not None:
            sop_import_node = hou.node("/stage/" + sop_import_node_name)

            # Trying to find "sopimport" node
            if sop_import_node.type().name() == "sopimport":
                result = 0
                # Show dialog only if "YES for all" is not active and paths differ
                if not flag and sop_import_node.parm("soppath").eval() != node.path():
                    result = hou.ui.displayMessage(
                        f"{sop_import_node.path()} already created and linked to "
                        f'{sop_import_node.parm("soppath").eval()}!!!\n \n'
                        f"Would you like to change link to {node.path()}?",
                        buttons=("YES", "YES for all", "Skip", "Cancel"),
                        close_choice=3,
                    )
                    if result == 1:  # "YES for all"
                        flag = True  # Set flag to True

                # Automatically apply if "YES for all" is active or user selects "YES"
                if result == 0 or result == 1:
                    for node in hou.node("/stage/" + sop_import_node_name).children():
                        if sop_import_node.type().name() == "sopimport":
                            node.parm("soppath").set(node.path())

                # Exit if user selects "Cancel"
                if result == 3:
                    exit()

            return flag  # Return updated flag

        # If the node doesn't exist, create a new SOP Import node
        else:
            new_sopimport_node = hou.node("/stage").createNode("sopimport")
            new_sopimport_node.setName(sop_import_node_name.upper(), unique_name=True)
            new_sopimport_node.setPosition(
                hou.Vector2(reference_node_pos[0] + 10, reference_node_pos[1] + offset)
            )
            new_sopimport_node.parm("soppath").set(node.path())
            new_sopimport_node.parm("asreference").set(True)
            # Enable and set absolute path prefix for the SOP Import node
            new_sopimport_node.parm("enable_prefixabsolutepaths").set(True)
            new_sopimport_node.parm("prefixabsolutepaths").set(True)

        return flag


    # Main script execution
    if not hou.selectedNodes():
        hou.ui.displayMessage("Before running Tool Please select nodes", title="WARNING")
        exit()

    # Ensure selected nodes are in the /obj context
    for el in hou.selectedNodes():
        if el.parent().name() != "obj":
            hou.ui.displayMessage(
                "Please select nodes only in /obj context", title="WARNING"
            )
            exit()

    # Determine the reference position for new nodes
    if len(hou.node("/stage/").children()) > 0:
        reference_node_pos = hou.node("/stage/").children()[0].position()
    else:
        reference_node_pos = [0, 0]

    offset = 0  # Offset for new nodes
    yes_for_all = False  # Flag to track "YES for all" state

    # Process each selected node
    for node in hou.selectedNodes():
        yes_for_all = create_sop_import_node(node, reference_node_pos, offset, yes_for_all)
        offset -= 1.2  # Adjust the offset for the next node
