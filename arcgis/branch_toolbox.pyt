# -*- coding: utf-8 -*-
"""branch ArcGIS Pro Python Toolbox.

REQUIRES ArcGIS Pro 3.x. This file imports ``arcpy``, which ships only inside
ArcGIS Pro's conda Python; it cannot run in a plain interpreter or CI. Add it to
a project via Catalog pane > Toolboxes > Add Toolbox, then run the "branch
Shade Analysis" tool.

The tool is a thin wrapper: it calls ``branch.arcgis_export.export_all`` to run
the shade pipeline and write shapefiles / GeoJSON, then loads the resulting
feature classes into the active map so the analyst sees them immediately.

Make the ``branch`` package importable from ArcGIS Pro's Python before running,
either by ``pip install -e .`` into the Pro conda environment or by pointing the
"branch repo folder" parameter at the checkout so the tool can extend sys.path.
"""
import os
import sys

import arcpy


class Toolbox(object):
    """The ArcGIS Pro toolbox container (one tool: branchShade)."""

    def __init__(self):
        self.label = "branch"
        self.alias = "branch"
        self.tools = [branchShade]


class branchShade(object):
    """Run branch shade analysis and load the outputs into the map."""

    def __init__(self):
        self.label = "branch Shade Analysis"
        self.description = ("Model street-tree shade for a study area and time, "
                            "then export scored walk segments, ranked planting "
                            "sites, trees, and the shade footprint as GIS layers.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        """Define the tool's four input parameters."""
        area = arcpy.Parameter(
            displayName="Study area",
            name="area",
            datatype="GPString",
            parameterType="Required",
            direction="Input")
        area.filter.type = "ValueList"
        area.filter.list = ["park_slope", "upper_west_side", "forest_hills"]
        area.value = "park_slope"

        when = arcpy.Parameter(
            displayName="Datetime (local, YYYY-MM-DD HH:MM)",
            name="when_local",
            datatype="GPString",
            parameterType="Required",
            direction="Input")
        when.value = "2026-07-15 15:00"

        alpha = arcpy.Parameter(
            displayName="Shade aversion (alpha)",
            name="alpha",
            datatype="GPDouble",
            parameterType="Required",
            direction="Input")
        alpha.value = 4.0

        out_dir = arcpy.Parameter(
            displayName="Output folder",
            name="out_dir",
            datatype="DEFolder",
            parameterType="Required",
            direction="Output")

        repo = arcpy.Parameter(
            displayName="branch repo folder (only if not pip-installed)",
            name="repo_dir",
            datatype="DEFolder",
            parameterType="Optional",
            direction="Input")

        return [area, when, alpha, out_dir, repo]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        """Run the export and add the shapefiles to the current map."""
        area_key = parameters[0].valueAsText
        when_local = parameters[1].valueAsText
        alpha = float(parameters[2].value)
        out_dir = parameters[3].valueAsText
        repo_dir = parameters[4].valueAsText

        # Let an analyst point at a source checkout instead of pip-installing.
        if repo_dir and repo_dir not in sys.path:
            sys.path.insert(0, repo_dir)

        try:
            from branch.config import AREAS
            from branch import arcgis_export
        except ImportError as exc:
            arcpy.AddError(
                "Could not import the 'branch' package. Either 'pip install -e .' "
                "it into the ArcGIS Pro Python environment or set the "
                "'branch repo folder' parameter. Details: {}".format(exc))
            raise

        area = AREAS[area_key]
        arcpy.AddMessage("Running branch for {} at {} (alpha={}) ...".format(
            area.name, when_local, alpha))

        written = arcgis_export.export_all(
            area, when_local=when_local, alpha=alpha, out_dir=out_dir)
        for path in written:
            arcpy.AddMessage("  wrote {}".format(path))

        self._add_to_map(out_dir)

    def _add_to_map(self, out_dir):
        """Load the exported shapefiles into the active map, if there is one."""
        shapefiles = [
            os.path.join(out_dir, "scored_segments.shp"),
            os.path.join(out_dir, "planting_gaps.shp"),
        ]
        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            active_map = aprx.activeMap
        except (OSError, RuntimeError):
            active_map = None

        if active_map is None:
            arcpy.AddWarning(
                "No active map; skipping layer load. The files are on disk in "
                + out_dir)
            return

        for shp in shapefiles:
            if not arcpy.Exists(shp):
                continue
            name = os.path.splitext(os.path.basename(shp))[0]
            # MakeFeatureLayer builds an in-memory layer; addDataFromPath is the
            # ArcGIS Pro way to drop a dataset straight onto the map.
            arcpy.management.MakeFeatureLayer(shp, name)
            active_map.addDataFromPath(shp)
            arcpy.AddMessage("  added layer {}".format(name))
