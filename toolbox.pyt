import arcpy
import os

class Toolbox(object):
    """Defines the toolbox (name, alias, and tools)."""
    def __init__(self):
        """Initialize the toolbox."""
        self.label = "Transfer Attributes and Geometry Toolbox"
        self.alias = "transfer_toolbox"
        self.tools = [TransferAttributesTool]  # List of tools in this toolbox


class TransferAttributesTool(object):
    """Transfer attributes and geometry between point datasets."""
    def __init__(self):
        """Initialize the tool."""
        self.label = "Transfer Attributes and Geometry"
        self.description = "Transfers attributes and geometry from GPS Source to GeoDatabase Source points."
        self.canRunInBackground = False

    def getParameterInfo(self):
        """Define parameter definitions."""
        params = []

        # Parameter 1: GPS Source Layer
        param0 = arcpy.Parameter(
            displayName="GPS Source Layer",
            name="gps_source_layer",
            datatype="Feature Layer",
            parameterType="Required",
            direction="Input"
        )
        params.append(param0)

        # Parameter 2: GeoDatabase Source Layer
        param1 = arcpy.Parameter(
            displayName="GeoDatabase Source Layer",
            name="gdb_source_layer",
            datatype="Feature Layer",
            parameterType="Required",
            direction="Input"
        )
        params.append(param1)

        # Parameter 3: Refresh Layers Option
        param2 = arcpy.Parameter(
            displayName="Refresh Layers in Map",
            name="refresh_layers",
            datatype="Boolean",
            parameterType="Optional",
            direction="Input"
        )
        param2.value = False  # Default to disabled
        params.append(param2)

        # Parameter 4: Refresh SDE Connections Option
        param3 = arcpy.Parameter(
            displayName="Refresh SDE Connections",
            name="refresh_sde",
            datatype="Boolean",
            parameterType="Optional",
            direction="Input"
        )
        param3.value = False  # Default to disabled
        params.append(param3)

        # Output Parameter (optional, e.g., status message)
        param4 = arcpy.Parameter(
            displayName="Output Message",
            name="output_message",
            datatype="String",
            parameterType="Derived",
            direction="Output"
        )
        params.append(param4)

        return params

    def isLicensed(self):
        """Set whether the tool is licensed to execute."""
        return True

    def execute(self, parameters, messages):
        """Main tool execution."""
        gps_source_layer = parameters[0].valueAsText
        gdb_source_layer = parameters[1].valueAsText
        refresh_layers = parameters[2].valueAsText.lower() == 'true'
        refresh_sde = parameters[3].valueAsText.lower() == 'true'

        try:
            # Create feature layers
            gps_selection = arcpy.MakeFeatureLayer_management(gps_source_layer, "gps_selection")
            gdb_selection = arcpy.MakeFeatureLayer_management(gdb_source_layer, "gdb_selection")

            # Ensure exactly one feature is selected in both layers
            gps_count = int(arcpy.GetCount_management(gps_selection)[0])
            gdb_count = int(arcpy.GetCount_management(gdb_selection)[0])

            if gps_count != 1 or gdb_count != 1:
                raise ValueError("You must select exactly one feature in both layers.")

            # Transfer attributes based on matching fields
            gps_fields = [field.name for field in arcpy.ListFields(gps_source_layer) if field.editable]
            gdb_fields = [field.name for field in arcpy.ListFields(gdb_source_layer) if field.editable]

            # Find common fields
            common_fields = set(gps_fields).intersection(gdb_fields)
            if not common_fields:
                raise ValueError("No common editable fields found between the two datasets.")

            # Convert the set to a list for cursor usage
            common_fields = list(common_fields)

            # Retrieve the workspace for the GeoDatabase source layer
            workspace, workspace_type = self.get_workspace_path(gdb_source_layer)
            arcpy.AddMessage(f"Workspace Path: {workspace}")
            arcpy.AddMessage(f"Workspace Type: {workspace_type}")

            # Start an edit session for the GeoDatabase Source
            edit = arcpy.da.Editor(workspace)
            edit.startEditing(False, True)  # Start edit session with undo/redo disabled
            edit.startOperation()  # Start an edit operation

            try:
                # Transfer attributes
                with arcpy.da.SearchCursor(gps_selection, common_fields) as gps_cursor, \
                     arcpy.da.UpdateCursor(gdb_selection, common_fields) as gdb_cursor:

                    gps_row = next(gps_cursor)  # Get the GPS source row
                    for gdb_row in gdb_cursor:  # Update the GeoDatabase row
                        for i, field in enumerate(common_fields):
                            gdb_row[i] = gps_row[i]
                        gdb_cursor.updateRow(gdb_row)

                # Replace geometry
                gps_geometry = [row[0] for row in arcpy.da.SearchCursor(gps_selection, "SHAPE@")][0]
                with arcpy.da.UpdateCursor(gdb_selection, "SHAPE@") as cursor:
                    for row in cursor:
                        row[0] = gps_geometry  # Replace geometry
                        cursor.updateRow(row)

                arcpy.AddMessage("Attributes and geometry successfully transferred.")

            except Exception as e:
                edit.abortOperation()  # Abort the operation on error
                raise e

            edit.stopOperation()  # Stop the operation
            edit.stopEditing(True)  # Save changes and stop the edit session

            # Conditional refresh
            if workspace_type == "File Geodatabase or Folder":
                arcpy.RefreshCatalog(workspace)
                arcpy.AddMessage("File Geodatabase or Folder has been refreshed.")
            else:
                arcpy.AddMessage("No refresh needed for Enterprise Geodatabase (.sde).")

            # Optionally refresh layers in the map
            if refresh_layers:
                aprx = arcpy.mp.ArcGISProject("CURRENT")
                for map_ in aprx.listMaps():
                    for layer in map_.listLayers():
                        try:
                            # Check if the layer is a valid feature layer
                            if layer.isFeatureLayer and gdb_source_layer in layer.name:
                                layer.definitionQuery = layer.definitionQuery  # Force refresh
                                arcpy.AddMessage(f"Layer '{layer.name}' refreshed.")
                        except Exception as e:
                            arcpy.AddWarning(f"Failed to refresh layer: {str(e)}")

            # Optionally refresh only the SDE layer used originally
            if refresh_sde:
                self.refresh_single_sde_connection(gdb_source_layer)

            arcpy.SetParameterAsText(4, "Transfer Completed")

        except Exception as e:
            arcpy.AddError(f"Error occurred: {e}")

    @staticmethod
    def get_workspace_path(layer):
        """
        Retrieves the correct workspace for a given layer.
        Handles cases where the layer is part of an enterprise geodatabase (SDE) or file geodatabase.
        """
        desc = arcpy.Describe(layer)
        workspace_type = "Unknown"
        
        if hasattr(desc, "catalogPath"):
            catalog_path = desc.catalogPath

            if ".sde" in catalog_path:
                sde_index = catalog_path.lower().find(".sde")
                workspace_path = catalog_path[:sde_index + 4]  # Include ".sde"
                workspace_type = "Enterprise Geodatabase (.sde)"
                return workspace_path, workspace_type
            else:
                workspace_path = os.path.dirname(catalog_path)
                workspace_type = "File Geodatabase or Folder"
                return workspace_path, workspace_type

        else:
            raise ValueError("Unable to determine workspace for the provided layer.")

    @staticmethod
    def refresh_single_sde_connection(target_layer):
        """Refresh the specific SDE connection for the provided target layer."""
        aprx = arcpy.mp.ArcGISProject("CURRENT")
        for map_ in aprx.listMaps():
            arcpy.AddMessage(f"Refreshing SDE connection in map: {map_.name}")
            for lyr in map_.listLayers():
                if lyr.supports("connectionProperties") and lyr.isFeatureLayer:
                    try:
                        if target_layer in lyr.name:
                            conn_props = lyr.connectionProperties
                            if "connection_info" in conn_props:
                                lyr.updateConnectionProperties(lyr.connectionProperties, conn_props)
                                arcpy.AddMessage(f"Layer '{lyr.name}' SDE connection refreshed.")
                                return  # Exit after refreshing the first matching layer
                    except Exception as e:
                        arcpy.AddWarning(f"Failed to refresh SDE connection for layer '{lyr.name}': {str(e)}")
        arcpy.AddWarning("No matching SDE layer found to refresh.")

    def updateParameters(self, parameters):
        """Modify parameter values before internal validation."""
        return

    def updateMessages(self, parameters):
        """Modify parameter messages after internal validation."""
        return
