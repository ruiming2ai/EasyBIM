# -*- coding: utf-8 -*-
"""Portable defaults for View Tab Colors.

The identifiers and Revit ViewType names are intentionally non-localized.
They are serialized by name rather than enum integer so a settings file can
move between supported Revit releases.
"""

from __future__ import absolute_import

import copy


SCHEMA = "viewtabcolors.settings"
SCHEMA_VERSION = 1


DEFAULT_RULES = [
    {
        "id": "three_d",
        "label": "3D / Walkthrough / Rendering",
        "enabled": True,
        "color": "#6FA8DC",
        "view_types": ["ThreeD", "Walkthrough", "Rendering"],
    },
    {
        "id": "floor_plan",
        "label": "Floor Plan",
        "enabled": True,
        "color": "#93C47D",
        "view_types": ["FloorPlan"],
    },
    {
        "id": "ceiling_plan",
        "label": "Ceiling Plan (RCP)",
        "enabled": True,
        "color": "#FFD966",
        "view_types": ["CeilingPlan"],
    },
    {
        "id": "engineering_plan",
        "label": "Engineering / Structural Plan",
        "enabled": True,
        "color": "#76A5AF",
        "view_types": ["EngineeringPlan"],
    },
    {
        "id": "area_plan",
        "label": "Area Plan",
        "enabled": True,
        "color": "#B4A7D6",
        "view_types": ["AreaPlan"],
    },
    {
        "id": "drafting",
        "label": "Drafting View",
        "enabled": True,
        "color": "#C27BA0",
        "view_types": ["DraftingView"],
    },
    {
        "id": "detail",
        "label": "Detail View",
        "enabled": True,
        "color": "#E69138",
        "view_types": ["Detail"],
    },
    {
        "id": "section",
        "label": "Section",
        "enabled": True,
        "color": "#E06666",
        "view_types": ["Section"],
    },
    {
        "id": "elevation",
        "label": "Elevation",
        "enabled": True,
        "color": "#F6B26B",
        "view_types": ["Elevation"],
    },
    {
        "id": "sheet",
        "label": "Sheet",
        "enabled": True,
        "color": "#8E7CC3",
        "view_types": ["DrawingSheet"],
    },
    {
        "id": "schedule",
        "label": "Schedule / Panel Schedule",
        "enabled": True,
        "color": "#A2C4C9",
        "view_types": ["Schedule", "PanelSchedule", "ColumnSchedule"],
    },
    {
        "id": "legend",
        "label": "Legend",
        "enabled": True,
        "color": "#D5A6BD",
        "view_types": ["Legend"],
    },
    {
        "id": "report",
        "label": "Reports",
        "enabled": True,
        "color": "#B7B7B7",
        "view_types": [
            "Report",
            "CostReport",
            "LoadsReport",
            "PresureLossReport",
            "SystemsAnalysisReport",
        ],
    },
    {
        "id": "other",
        "label": "Other / Unknown View Type",
        "enabled": False,
        "color": "#D9D9D9",
        "view_types": [
            "Undefined",
            "Internal",
            "ProjectBrowser",
            "SystemBrowser",
        ],
    },
]


def make_default_profile():
    """Return a new, independent default profile."""
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        # Tab coloring is an explicit opt-in because it changes Revit's UI.
        "enabled": False,
        "rules": copy.deepcopy(DEFAULT_RULES),
    }
