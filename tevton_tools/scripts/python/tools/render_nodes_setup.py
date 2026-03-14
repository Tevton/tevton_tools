def create_render_node_setup():
    import hou


    # Creating geo node with {render_prefix} prefix and setup node network
    def create_render_node_network(out_node, reference_node_pos, offset, flag):
        # Checking for already created {render_prefix} nodes
        render_node_name = render_prefix + out_node.name().removeprefix(out_prefix).upper()
        if hou.node("/obj/" + render_node_name) is not None:
            for node in hou.node("/obj/" + render_node_name).children():
                # Trying to find "obj_merge" node
                if node.type().name() == "object_merge":
                    objmerge_node = node
                    result = 0
                    # Show dialog only if "YES for all" is not active and paths differ
                    if (
                        not flag
                        and objmerge_node.parm("objpath1").eval() != out_node.path()
                    ):
                        result = hou.ui.displayMessage(
                            f'{hou.node("/obj/" + render_node_name)} already created and linked to '
                            f'{objmerge_node.parm("objpath1").eval()}!!!\n \n'
                            f"Would you like to change link to {out_node.path()}?",
                            buttons=("YES", "YES for all", "Skip", "Cancel"),
                            close_choice=3,
                        )
                        if result == 1:  # "YES for all"
                            flag = True  # Set flag to True

                    if result == 2:  # Skip current node
                        continue

                    # Automatically apply if "YES for all" is active or user selects "YES"
                    if result == 0 or result == 1:
                        for node in hou.node("/obj/" + render_node_name).children():
                            if node.type().name() == "object_merge":
                                node.parm("objpath1").set(out_node.path())

                    # Exit if user selects "Cancel"
                    if result == 3:
                        exit()
            return flag  # Return updated flag

        # Creating node setup in render node
        else:
            # Createing geo node with name of output node
            new_geo_node = hou.node("/obj").createNode("geo")
            new_geo_node.setColor(hou.Color(0.451, 0.369, 0.796))
            new_geo_node.setName(
                render_prefix + out_node.name().upper().removeprefix(out_prefix),
                unique_name=True,
            )
            get_geo_node_pos = out_node.parent().position()
            new_geo_node.setPosition(
                hou.Vector2(reference_node_pos[0] + 4, reference_node_pos[1] + offset)
            )
            new_geo_node.setGenericFlag(hou.nodeFlag.Display, 0)

            # Creating null node with name of render node
            new_null_node = hou.node(new_geo_node.path()).createNode("null")
            new_null_node.setColor(hou.Color(0.451, 0.369, 0.796))
            new_null_node.setName(new_geo_node.name())
            new_null_node.setGenericFlag(hou.nodeFlag.Render, 1)

            # Creating objmerge node
            new_objmerge_node = hou.node(new_geo_node.path()).createNode("object_merge")
            new_objmerge_node.setColor(hou.Color(1, 0, 0))
            get_null_node_pos = new_null_node.position()
            new_objmerge_node.setPosition(
                hou.Vector2(get_null_node_pos[0], get_null_node_pos[1] + 3)
            )

            # Set path to output null node
            new_objmerge_node.parm("objpath1").set(out_node.path())

            # Connecting objmerge and null node
            new_null_node.setInput(0, new_objmerge_node)
            return flag  # Return updated flag


    # Find bottom node position
    def find_min_child_pos(node):
        nodes = node.children()
        for n in nodes:
            # Find the minimum Y coordinate among all child nodes
            min_y_pos = min(n.position()[1] for n in nodes)
            if n.position()[1] == min_y_pos:
                get_n_pos = n.position()
                return get_n_pos


    # Creating or changing name of null in selected nodes below bottom node with {out_prefix}
    def setup_children_null(child):
        name = child.name().upper()
        new_null_node = hou.node(child.parent().path()).createNode("null")
        new_null_node.setColor(hou.Color(0.451, 0.369, 0.796))
        new_null_node.setPosition(hou.Vector2(child.position()[0], child.position()[1] - 2))
        new_null_node.setInput(0, child)
        if name.startswith("OUT_"):
            new_null_node.setName(out_prefix + name.removeprefix("OUT_"), unique_name=True)
        else:
            new_null_node.setName(out_prefix + name, unique_name=True)
        return new_null_node


    ## Main script body ##
    # Checking for selected nodes
    if not hou.selectedNodes():
        hou.ui.displayMessage("Before running Tool Please select nodes", title="WARNING")
        exit()

    # Checking selected nodes for context
    for el in hou.selectedNodes():
        if el.parent().name() != "obj":
            hou.ui.displayMessage(
                "Please select nodes only in /obj context", title="WARNING"
            )
            exit()
    else:
        init_out_prefix = "RENDER_"  # Prefix for user output nodes
        init_render_prefix = "RENDER_"  # Prefix for render nodes

        # Ask user for initialize prefixes
        result, values = hou.ui.readMultiInput(
            "Please specify prefixes:",
            ("YOUR output prefix", "Render Node prefix"),
            buttons=("OK", "Cancel"),
            close_choice=1,
            title="Render nodes Setup",
            initial_contents=(f"{init_out_prefix}", f"{init_render_prefix}"),
        )
        out_prefix = str(values[0])  # User initialized output prefix for nodes
        render_prefix = str(values[1])  # User initialized render prefix for nodes

        if result == 1:  # 'Cancel'
            exit()
        else:
            offset = 0  # Offset for new nodes
            reference_node_pos = hou.selectedNodes()[
                0
            ].position()  # Position for creating new nodes
            yes_for_all = False  # Flag to track first "YES for All" state
            yes_for_all2 = False  # Flag to track second "YES for All" state
            yes_for_all3 = False  # Flag to track "YES for All" state in {create_render_node_network} function

            # Checking selected nodes
            for node in hou.selectedNodes():
                # Check for node type "geo" and node name, if name starts with {render_prefix}, skip node
                if node.type().name() == "geo" and not node.name().upper().startswith(
                    out_prefix
                ):
                    min_child_pos = find_min_child_pos(node)

                    # Trying to find null nodes with {out_prefix} and ask user to make some actions
                    for child in node.children():
                        if child.type().name() == "null":
                            if child.name().upper().startswith(out_prefix):
                                yes_for_all3 = create_render_node_network(
                                    child, reference_node_pos, offset, yes_for_all3
                                )
                                offset -= 1
                            if (
                                not child.name().upper().startswith(out_prefix)
                                and min_child_pos[1] == child.position()[1]
                            ):
                                if (
                                    not yes_for_all
                                ):  # Show dialog only if first "YES for All" is not active
                                    result = hou.ui.displayMessage(
                                        f'In {node} detected bottom "null" node {child.name()} without {out_prefix} prefix!!!\n \n'
                                        f'Would you like to create "null" node with prefix {out_prefix}?',
                                        buttons=("YES", "YES for All", "Skip", "Cancel"),
                                        close_choice=3,
                                        title="WARNING",
                                    )
                                    if result == 1:  # "YES for all"
                                        yes_for_all = (
                                            True  # Flag to track first "YES for All" state
                                        )
                                if result == 2:  # Skip current node
                                    continue
                                if (
                                    result == 0 or result == 1
                                ):  # Automatically apply if first "YES for All" is active
                                    new_null_node = setup_children_null(child)
                                    yes_for_all3 = create_render_node_network(
                                        new_null_node,
                                        reference_node_pos,
                                        offset,
                                        yes_for_all3,
                                    )
                                    offset -= 1
                                if result == 3:  # "Cancel"
                                    exit()

                        # Find bottom nodes position and ask user to create null nodes under them
                        if (
                            min_child_pos[1] == child.position()[1]
                            and child.type().name() != "null"
                        ):
                            if (
                                not yes_for_all2
                            ):  # Show dialog only if second "YES for All" is not active
                                result = hou.ui.displayMessage(
                                    f'Cannot find any bottom "null" nodes in {node} \n \n'
                                    "Would you like to create output nodes for current node?",
                                    buttons=("YES", "Yes for all", "Skip", "Cancel"),
                                    close_choice=3,
                                    details=f"Tool will create null node under bottom node with {out_prefix} prefix",
                                    title="WARNING",
                                )
                                if result == 1:  # "YES for all"
                                    yes_for_all2 = (
                                        True  # Flag to track second "YES for All" state
                                    )
                            if result == 2:  # Skip current node
                                continue
                            if (
                                result == 0 or result == 1
                            ):  # Automatically apply if second "YES for All" is active
                                new_null_node = setup_children_null(child)
                                yes_for_all3 = create_render_node_network(
                                    new_null_node, reference_node_pos, offset, yes_for_all3
                                )
                                offset -= 1
                            if result == 3:  # "Cancel"
                                exit()
