def batch_import():
    import hou


    default_directory = hou.text.expandString("$HIP")
    select_files = hou.ui.selectFile(start_directory=default_directory, 
                                    title="Select the files to import",
                                    file_type=hou.fileType.Geometry,
                                    multiple_select=True)
    if select_files:                                
        select_files = select_files.split(";")
        obj = hou.node('/obj')
        geo_node = obj.createNode('geo', node_name='temp_geo')
        merge_node = geo_node.createNode('merge')
        
        merge_counter = 0
        
        for file in select_files:
            file = file.strip()
            name, extention = file.split("/")[-1].split(".")
            
            if extention == 'abc':
                new_node = geo_node.createNode('alembic', node_name=name)
                new_node.parm('fileName').set(file)
                
                new_unpack_node = geo_node.createNode('unpack', node_name=name + "_unpack")
                new_unpack_node.setInput(0, new_node)
                
                transform_node = geo_node.createNode('xform', node_name=name + "_scale")
                transform_node.parm('scale').set(0.01)
                transform_node.setInput(0, new_unpack_node)
                
            else:
                new_node = geo_node.createNode('file', node_name=name)
                new_node.parm('file').set(file)
                
                transform_node = geo_node.createNode('xform', node_name=name + "_scale")
                transform_node.parm('scale').set(0.01)
                transform_node.setInput(0, new_node)
        
            merge_node.setInput(merge_counter, transform_node)
            merge_counter += 1
        
        geo_node.layoutChildren()