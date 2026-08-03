# -*- coding: utf-8 -*-
"""Excel writer for the Export Schedules to Excel command.

Ported from pyRevit's "Export Parameters to Excel" (XLS Export.pushbutton,
xlsex_script.py) so the produced workbooks stay compatible with pyRevit's
"Import Parameters from Excel": ElementId column, one column per visible
schedule field, and a hidden _metadata sheet. The one intentional change is
that rows are written in the caller-provided (schedule display) order instead
of raw collector order.
"""

# pylint: disable=import-error,invalid-name,broad-except
import os
import re
from collections import namedtuple

from pyrevit import DB
from pyrevit import coreutils
from pyrevit import revit
from pyrevit import script
from pyrevit.framework import System

from easybim import print_sets

try:
    import xlsxwriter
    XLSXWRITER_AVAILABLE = True
except Exception:
    xlsxwriter = None
    XLSXWRITER_AVAILABLE = False

try:
    from pyrevit.revit import get_parameter_data_type
except Exception:
    def get_parameter_data_type(definition):
        try:
            return definition.GetDataType()
        except Exception:
            return None

try:
    from pyrevit.revit import is_yesno_parameter
except Exception:
    def is_yesno_parameter(definition):
        try:
            return definition.GetDataType() == DB.SpecTypeId.Boolean.YesNo
        except Exception:
            return False


LOGGER = script.get_logger()

BIP = DB.BuiltInParameter

try:
    measurable = DB.UnitUtils.IsMeasurableSpec
except Exception:
    def measurable(forge_type_id):
        del forge_type_id
        return False

get_elementid_value = print_sets.get_element_id_value

try:
    from xlsxwriter.utility import xl_col_to_name
except Exception:
    def xl_col_to_name(col_idx):
        name = ""
        index = int(col_idx)
        while index >= 0:
            name = chr(index % 26 + ord("A")) + name
            index = index // 26 - 1
        return name

VBA_BIN_PATH = os.path.join(os.path.dirname(__file__), "vba", "vbaProject.bin")


def vba_available():
    """True when the user-authored VBA sync project is in the bundle."""
    try:
        return os.path.isfile(VBA_BIN_PATH)
    except Exception:
        return False


unit_postfix_pattern = re.compile(r"\s*\[.*\]$")

# One colour per user action. Light tints so values stay readable; the
# Legend sheet documents every one of them.
COLOR_LOCKED = "#F2F2F2"        # grey  - not editable / never imported
COLOR_TYPE = "#FFF2CC"          # yellow - type parameter, shared by the type
COLOR_WARNING = "#FFE5B4"       # amber - will not import as typed
COLOR_WARNING_TEXT = "#843C0C"
COLOR_CONFLICT = "#FFC7CE"      # red   - blocks or corrupts the import
COLOR_CONFLICT_TEXT = "#9C0006"
COLOR_HEADER_READONLY = "#FF3131"
COLOR_HEADER_ELEMENTID = "#FFBD80"
COLOR_HEADER_REFERENCE = "#BFBFBF"

# Family/type parameters are exported for reference but never imported:
# resolving them by name is ambiguous and Revit raises cannot-be-ignored
# errors for some categories. Keep this list in step with
# import_excel_revit.TYPE_CHANGING_BIP_NAMES.
REFERENCE_ONLY_BIP_NAMES = (
    "ELEM_FAMILY_AND_TYPE_PARAM",
    "ELEM_TYPE_PARAM",
    "ELEM_FAMILY_PARAM",
)


def _is_reference_only_param(param_def):
    """True for Family / Type / Family and Type columns."""
    try:
        definition_bip = getattr(param_def.definition, "BuiltInParameter", None)
    except Exception:
        return False
    if definition_bip is None:
        return False
    for name in REFERENCE_ONLY_BIP_NAMES:
        bip = getattr(BIP, name, None)
        if bip is not None and definition_bip == bip:
            return True
    return False


def _is_workset_param(param_def):
    try:
        return param_def.definition.BuiltInParameter == BIP.ELEM_PARTITION_PARAM
    except Exception:
        return False

ParamDef = namedtuple(
    "ParamDef",
    [
        "name",
        "istype",
        "definition",
        "isreadonly",
        "isunit",
        "storagetype",
        "forge_type_id",
        "unit_type_id",
    ],
)


def get_unit_label_and_value(param, forge_type_id, field, project_units):
    """Return (converted_value, label, unit_type_id, symbol_type_id).

    Respects schedule field format overrides, otherwise uses project units.
    """
    if not forge_type_id or not measurable(forge_type_id):
        return param.AsValueString(), "", None, None

    fmt_opts = None
    if field:
        try:
            field_fmt = field.GetFormatOptions()
            if field_fmt and not field_fmt.UseDefault:
                fmt_opts = field_fmt
        except Exception:
            pass

    if not fmt_opts:
        fmt_opts = project_units.GetFormatOptions(forge_type_id)

    if fmt_opts.UseDefault:
        fmt_opts = project_units.GetFormatOptions(forge_type_id)

    unit_id = fmt_opts.GetUnitTypeId()
    symbol_id = fmt_opts.GetSymbolTypeId()

    val = DB.UnitUtils.ConvertFromInternalUnits(param.AsDouble(), unit_id)

    label = ""
    try:
        label = DB.LabelUtils.GetLabelForSymbol(symbol_id)
    except Exception:
        try:
            label = DB.LabelUtils.GetLabelForUnit(unit_id)
        except Exception:
            label = ""

    return val, "[{}]".format(label) if label else "", unit_id, symbol_id


def _field_name(field):
    try:
        return field.GetName()
    except Exception:
        return ""


def _bip_from_id_value(id_value):
    """Return the BuiltInParameter for a negative parameter id value."""
    try:
        id_int = int(id_value)
    except Exception:
        return None
    if id_int >= 0:
        return None
    try:
        return System.Enum.ToObject(DB.BuiltInParameter, id_int)
    except Exception:
        return None


def _lookup_field_param(owner, field, param_id_value):
    """Find the parameter backing a schedule field on the given element."""
    bip = _bip_from_id_value(param_id_value)
    if bip is not None:
        try:
            param = owner.get_Parameter(bip)
            if param is not None:
                return param
        except Exception:
            pass
    field_name = _field_name(field)
    if field_name:
        try:
            return owner.LookupParameter(field_name)
        except Exception:
            return None
    return None


def _lookup_param_by_def(owner, param_def):
    try:
        param = owner.get_Parameter(param_def.definition)
        if param is not None:
            return param
    except Exception:
        pass
    try:
        return owner.LookupParameter(param_def.name)
    except Exception:
        return None


def _get_type_element(doc, element, type_cache):
    try:
        type_id = element.GetTypeId()
    except Exception:
        return None
    if type_id is None or type_id == DB.ElementId.InvalidElementId:
        return None
    type_key = get_elementid_value(type_id)
    if type_key not in type_cache:
        try:
            type_cache[type_key] = doc.GetElement(type_id)
        except Exception:
            type_cache[type_key] = None
    return type_cache[type_key]


def _lookup_row_param(doc, element, param_def, type_cache):
    """Resolve a row value parameter: instance first, then element type."""
    param_el = _lookup_param_by_def(element, param_def)
    if param_el is None:
        type_el = _get_type_element(doc, element, type_cache)
        if type_el is not None:
            param_el = _lookup_param_by_def(type_el, param_def)
    return param_el


def _type_key_text(element):
    """Text key of the element's type id; empty for typeless elements."""
    try:
        type_id = element.GetTypeId()
        if type_id is not None and type_id != DB.ElementId.InvalidElementId:
            id_value = get_elementid_value(type_id)
            if id_value is not None:
                return str(id_value)
    except Exception:
        pass
    return ""


def _add_type_conflict_formatting(workbook, worksheet, valid_params, type_col,
                                  row_count):
    """Turn type-parameter cells red when same-type rows disagree."""
    # Excel's standard "Bad" style: light red fill, dark red text -
    # loud enough to notice, light enough to keep the value readable.
    conflict_fmt = workbook.add_format(
        {"bg_color": "#FFC7CE", "font_color": "#9C0006"}
    )
    first_row = 2
    last_row = row_count + 1
    type_col_name = xl_col_to_name(type_col)
    for col_idx, param in enumerate(valid_params):
        if not param.istype or param.isreadonly:
            continue
        col_name = xl_col_to_name(col_idx + 1)
        criteria = (
            '=AND(${t}{fr}<>"",'
            'COUNTIFS(${t}${fr}:${t}${lr},${t}{fr},'
            '${c}${fr}:${c}${lr},"<>"&${c}{fr})>0)'
        ).format(t=type_col_name, c=col_name, fr=first_row, lr=last_row)
        try:
            worksheet.conditional_format(
                1, col_idx + 1, row_count, col_idx + 1,
                {"type": "formula", "criteria": criteria,
                 "format": conflict_fmt},
            )
        except Exception as fmt_error:
            LOGGER.warning(
                "Could not add conflict formatting for '{}': {}".format(
                    param.name, fmt_error
                )
            )


def _add_edited_id_formatting(workbook, worksheet, row_count, rowid_col):
    """Turn the ElementId red if it no longer matches the exported id.

    Column A is how every row finds its element; an edited id silently
    writes one row's values onto a different element.
    """
    conflict_fmt = workbook.add_format(
        {"bg_color": COLOR_CONFLICT, "font_color": COLOR_CONFLICT_TEXT,
         "bold": True}
    )
    rowid_name = xl_col_to_name(rowid_col)
    criteria = '=$A2<>${r}2'.format(r=rowid_name)
    try:
        worksheet.conditional_format(
            1, 0, row_count, 0,
            {"type": "formula", "criteria": criteria, "format": conflict_fmt},
        )
    except Exception as fmt_error:
        LOGGER.warning(
            "Could not add ElementId formatting: {}".format(fmt_error)
        )


def _add_validation_formatting(workbook, worksheet, valid_params, row_count,
                               dropdown_sources):
    """Flag cells that will not import the way they look.

    Every rule reads a single cell (dropdown rules also read a short lookup
    list), so unlike the conflict rule the cost does not grow with the row
    count.
    """
    warning_fmt = workbook.add_format(
        {"bg_color": COLOR_WARNING, "font_color": COLOR_WARNING_TEXT}
    )
    for col_idx, param in enumerate(valid_params):
        if param.isreadonly or _is_reference_only_param(param):
            continue

        cell = "${}{}".format(xl_col_to_name(col_idx + 1), 2)
        # Never flag empties or the export's own placeholders.
        guard = (
            '{c}<>"",{c}<>"<does not exist>",{c}<>"<unsupported>",'
            '{c}<>"<Error>"'
        ).format(c=cell)
        rules = []
        source = dropdown_sources.get(param.name)

        if source:
            rules.append(
                '=AND({g},COUNTIF({s},{c})=0)'.format(g=guard, s=source, c=cell)
            )
        elif _is_workset_param(param) \
                or param.storagetype == DB.StorageType.ElementId:
            # Free-text element/workset names with no list to check against.
            continue
        elif param.storagetype == DB.StorageType.String:
            # Text columns carry a Text number format, so a numeric cell
            # means a paste re-formatted it and Excel ate the value
            # ("010" -> 10, "3-1" -> a date serial).
            rules.append('=AND({g},ISNUMBER({c}))'.format(g=guard, c=cell))
            rules.append(
                '=AND({g},OR(LEFT({c},1)=" ",RIGHT({c},1)=" "))'
                .format(g=guard, c=cell)
            )
        elif param.storagetype == DB.StorageType.Integer \
                and is_yesno_parameter(param.definition):
            rules.append(
                '=AND({g},{c}<>"Yes",{c}<>"No")'.format(g=guard, c=cell)
            )
        elif param.storagetype in (DB.StorageType.Double,
                                   DB.StorageType.Integer):
            rules.append('=AND({g},NOT(ISNUMBER({c})))'.format(g=guard, c=cell))

        for criteria in rules:
            try:
                worksheet.conditional_format(
                    1, col_idx + 1, row_count, col_idx + 1,
                    {"type": "formula", "criteria": criteria,
                     "format": warning_fmt},
                )
            except Exception as fmt_error:
                LOGGER.warning(
                    "Could not add validation formatting for '{}': {}".format(
                        param.name, fmt_error
                    )
                )


LEGEND_CELL_ROWS = (
    ("", COLOR_LOCKED, "Not editable", None,
     "The ElementId, read-only parameters, and values that do not apply to "
     "this element. Nothing in a grey cell is ever imported."),
    ("", None, "Editable", None,
     "A normal instance value. Type a new value to change that one element."),
    ("", COLOR_TYPE, "Type parameter", None,
     "Shared by every element of this type. Changing one row changes them "
     "all - including elements that are not in this schedule."),
    ("", COLOR_WARNING, "Check this value", COLOR_WARNING_TEXT,
     "It will not import as typed: wrong kind of value for the column, a "
     "name that is not in the dropdown, or stray spaces. Fix it first."),
    ("", COLOR_CONFLICT, "Blocks the import", COLOR_CONFLICT_TEXT,
     "Rows of the same type disagree on a type parameter, or the ElementId "
     "was edited. The import stops and reports these before writing."),
)

LEGEND_HEADER_ROWS = (
    ("", COLOR_HEADER_READONLY, "Read-only parameter", None,
     "Revit will not accept a new value. Exported for reference only."),
    ("", COLOR_HEADER_ELEMENTID, "Points at another element", None,
     "Type the element's name exactly as Revit shows it, or pick from the "
     "cell's dropdown list."),
    ("", COLOR_HEADER_REFERENCE, "Family / Type", None,
     "Exported for reference only and never imported. Change an element's "
     "family or type in Revit, not here."),
)

LEGEND_RULES = (
    "Clearing a cell CLEARS that value in Revit when you import. Where "
    "Revit refuses to leave a parameter unset, the nearest empty state is "
    "used instead: a Yes/No turns to No and a reference turns to None. "
    "Plain numbers are never zeroed - 0 is a real measurement, so those "
    "are listed in the import report rather than guessed at.",
    "Blanking one row of a type parameter while another row of the same "
    "type still holds a value is a conflict, and stops the import.",
    "Never edit, sort or delete column A (ElementId) - it is how each row "
    "finds its element. Edited ids turn red. Hiding it is fine; the import "
    "reads hidden columns and rows too.",
    "Hidden columns and sheets whose names start with an underscore are "
    "internal bookkeeping. Leave them alone.",
    "To load your edits back: in Revit open the Excel tool, tick the same "
    "schedule, then click \"Import to Schedule\".",
)


def _write_legend_sheet(workbook, legend_sheet):
    """Second sheet: what every colour in the workbook means."""
    title_fmt = workbook.add_format({"bold": True, "font_size": 16})
    section_fmt = workbook.add_format({"bold": True, "font_size": 12})
    label_fmt = workbook.add_format({"bold": True, "valign": "top"})
    text_fmt = workbook.add_format({"text_wrap": True, "valign": "top"})

    legend_sheet.set_column(0, 0, 6)
    legend_sheet.set_column(1, 1, 26)
    legend_sheet.set_column(2, 2, 78)
    legend_sheet.hide_gridlines(2)

    row = 0
    legend_sheet.write(row, 0, "How to read this workbook", title_fmt)
    row += 2

    legend_sheet.write(row, 0, "Cell colours", section_fmt)
    row += 1
    for _, color, label, font_color, description in LEGEND_CELL_ROWS:
        swatch = {"border": 1, "border_color": "#BFBFBF"}
        if color:
            swatch["bg_color"] = color
        if font_color:
            swatch["font_color"] = font_color
        legend_sheet.write_blank(row, 0, None, workbook.add_format(swatch))
        legend_sheet.write(row, 1, label, label_fmt)
        legend_sheet.write(row, 2, description, text_fmt)
        legend_sheet.set_row(row, 30)
        row += 1

    row += 1
    legend_sheet.write(row, 0, "Column heading colours", section_fmt)
    row += 1
    for _, color, label, font_color, description in LEGEND_HEADER_ROWS:
        swatch = {"border": 1, "border_color": "#BFBFBF", "bold": True}
        if color:
            swatch["bg_color"] = color
        if font_color:
            swatch["font_color"] = font_color
        legend_sheet.write_blank(row, 0, None, workbook.add_format(swatch))
        legend_sheet.write(row, 1, label, label_fmt)
        legend_sheet.write(row, 2, description, text_fmt)
        legend_sheet.set_row(row, 30)
        row += 1

    row += 1
    legend_sheet.write(row, 0, "Worth knowing", section_fmt)
    row += 1
    for rule in LEGEND_RULES:
        legend_sheet.write(row, 1, "-", label_fmt)
        legend_sheet.write(row, 2, rule, text_fmt)
        legend_sheet.set_row(row, 30)
        row += 1

    legend_sheet.protect("")


def collect_schedule_param_defs(schedule, elements):
    """Build ParamDefs for the schedule's visible non-calculated fields.

    Columns follow GetFieldOrder(); each ParamDef is derived from the first
    element (or, for type parameters, the first element type) carrying the
    field's parameter. Returns (param_defs, field_mapping).
    """
    schedule_def = schedule.Definition
    doc = schedule.Document

    visible_fields = []
    param_defs_list = []
    field_mapping = {}
    non_storage_type = coreutils.get_enum_none(DB.StorageType)
    processed_params = set()
    type_cache = {}

    for field_id in schedule_def.GetFieldOrder():
        field = schedule_def.GetField(field_id)
        if field.IsHidden:
            continue

        if field.IsCalculatedField:
            LOGGER.warning(
                "Skipping calculated field: {}".format(_field_name(field))
            )
            continue

        param_id = field.ParameterId
        if param_id and param_id != DB.ElementId.InvalidElementId:
            visible_fields.append(field)

    if not elements:
        raise ValueError("No elements found in schedule")

    for field in visible_fields:
        param_id_value = get_elementid_value(field.ParameterId)
        param = None
        istype = False

        for el in elements:
            param = _lookup_field_param(el, field, param_id_value)
            if param is not None:
                break

        if param is None:
            checked_type_keys = set()
            for el in elements:
                type_el = _get_type_element(doc, el, type_cache)
                if type_el is None:
                    continue
                type_key = get_elementid_value(type_el.Id)
                if type_key in checked_type_keys:
                    continue
                checked_type_keys.add(type_key)
                param = _lookup_field_param(type_el, field, param_id_value)
                if param is not None:
                    istype = True
                    break

        if param is None or param.StorageType == non_storage_type:
            LOGGER.warning(
                "Skipping field with no matching parameter: {}".format(
                    _field_name(field)
                )
            )
            continue

        def_name = param.Definition.Name
        if def_name in processed_params:
            continue

        param_data_type = get_parameter_data_type(param.Definition)

        unit_type_id = None
        if param_data_type and measurable(param_data_type):
            try:
                fmt_opts = field.GetFormatOptions()
                if fmt_opts:
                    unit_type_id = fmt_opts.GetUnitTypeId()
            except Exception:
                pass

        param_defs_list.append(ParamDef(
            name=def_name,
            istype=istype,
            definition=param.Definition,
            isreadonly=param.IsReadOnly,
            isunit=(
                measurable(param_data_type)
                if param_data_type
                else False
            ),
            storagetype=param.StorageType,
            forge_type_id=param_data_type,
            unit_type_id=unit_type_id,
        ))
        field_mapping[def_name] = field
        processed_params.add(def_name)

    if not param_defs_list:
        raise ValueError("No valid parameters found in schedule")

    return param_defs_list, field_mapping


def create_dropdown_validation(doc, worksheet, workbook, col_idx, param,
                               src_elements, hidden_sheets):
    """Add dropdown validation for ElementId and Workset parameters.

    Returns the list's range (e.g. "_worksets!$A$1:$A$12") so the caller can
    also flag pasted values that bypassed the dropdown, or None.
    """
    storage_type = param.storagetype

    if storage_type == DB.StorageType.Integer:
        if param.definition.BuiltInParameter == BIP.ELEM_PARTITION_PARAM:
            try:
                worksets = DB.FilteredWorksetCollector(doc)
                workset_names = sorted(
                    [ws.Name for ws in worksets if ws.Name]
                )

                if workset_names:
                    hidden_sheet_name = "_worksets"

                    if hidden_sheet_name not in hidden_sheets:
                        hidden_sheet = workbook.add_worksheet(hidden_sheet_name)
                        hidden_sheet.hide()
                        hidden_sheets[hidden_sheet_name] = hidden_sheet

                        for idx, name in enumerate(workset_names):
                            hidden_sheet.write(idx, 0, name)

                    formula = "={}!$A$1:$A${}".format(
                        hidden_sheet_name, len(workset_names)
                    )

                    worksheet.data_validation(
                        1,
                        col_idx,
                        len(src_elements),
                        col_idx,
                        {
                            "validate": "list",
                            "source": formula,
                            "error_message": "Please select a valid workset",
                            "error_type": "stop",
                        },
                    )
                    return formula.lstrip("=")

            except Exception as e:
                LOGGER.warning(
                    "Could not create workset dropdown for '{}': {}".format(
                        param.name, e
                    )
                )

    elif storage_type == DB.StorageType.ElementId:
        try:
            sample_category = None
            sample_type_cache = {}

            for el in src_elements:
                param_el = _lookup_row_param(doc, el, param, sample_type_cache)
                if param_el and param_el.HasValue:
                    el_id = param_el.AsElementId()
                    if el_id and el_id != DB.ElementId.InvalidElementId:
                        ref_element = doc.GetElement(el_id)
                        if (
                            ref_element
                            and hasattr(ref_element, "Category")
                            and ref_element.Category
                        ):
                            sample_category = \
                                ref_element.Category.BuiltInCategory
                            break

            if sample_category is not None:
                collector = revit.query.get_elements_by_categories(
                    [sample_category], doc=doc
                )
                element_names = sorted(
                    list(
                        set(
                            [
                                el.Name
                                for el in collector
                                if hasattr(el, "Name") and el.Name
                            ]
                        )
                    )
                )

                if element_names:
                    safe_param_name = (
                        re.sub(r"[^\w\s-]", "", param.name)
                        .strip()
                        .replace(" ", "_")
                    )
                    hidden_sheet_name = "_elem_{}".format(safe_param_name[:25])

                    if hidden_sheet_name not in hidden_sheets:
                        hidden_sheet = workbook.add_worksheet(hidden_sheet_name)
                        hidden_sheet.hide()
                        hidden_sheets[hidden_sheet_name] = hidden_sheet

                        for idx, name in enumerate(element_names):
                            hidden_sheet.write(idx, 0, name)

                    formula = "={}!$A$1:$A${}".format(
                        hidden_sheet_name, len(element_names)
                    )

                    worksheet.data_validation(
                        1,
                        col_idx,
                        len(src_elements),
                        col_idx,
                        {
                            "validate": "list",
                            "source": formula,
                            "error_message": "Please select a valid element",
                            "error_type": "stop",
                        },
                    )
                    return formula.lstrip("=")

        except Exception as e:
            LOGGER.warning(
                "Could not create ElementId dropdown for '{}': {}".format(
                    param.name, e
                )
            )

    return None


def _write_workbook(doc, src_elements, valid_params, field_mapping,
                    workbook, project_units, has_vba=False):
    worksheet = workbook.add_worksheet("Export")
    if has_vba:
        try:
            worksheet.set_vba_name("Sheet1")
        except Exception as vba_name_error:
            LOGGER.warning("Could not set VBA sheet name: %s", vba_name_error)
    legend_sheet = workbook.add_worksheet("Legend")
    metadata_sheet = workbook.add_worksheet("_metadata")
    metadata_sheet.hide()

    hidden_sheets = {}

    bold = workbook.add_format({"bold": True})
    # Grey = protected/non-editable; yellow = editable TYPE parameter
    # (shared by every row of the type; conflict formatting paints it red).
    # Light tints on purpose - cell values must stay easy to read.
    locked = workbook.add_format(
        {"locked": True, "bg_color": COLOR_LOCKED}
    )
    # Text columns carry an explicit Text number format so Excel stops
    # eating typed values ("010" -> 10, "3-1" -> a date serial).
    editable_formats = {
        (False, False): workbook.add_format({"locked": False}),
        (False, True): workbook.add_format(
            {"locked": False, "num_format": "@"}
        ),
        (True, False): workbook.add_format(
            {"locked": False, "bg_color": COLOR_TYPE}
        ),
        (True, True): workbook.add_format(
            {"locked": False, "bg_color": COLOR_TYPE, "num_format": "@"}
        ),
    }

    def _editable_format(param_def):
        return editable_formats[(
            bool(param_def.istype),
            param_def.storagetype == DB.StorageType.String,
        )]

    worksheet.freeze_panes(1, 0)
    worksheet.write(0, 0, "ElementId", bold)

    metadata_sheet.write(0, 0, "Parameter Name", bold)
    metadata_sheet.write(0, 1, "ForgeTypeId", bold)
    metadata_sheet.write(0, 2, "UnitTypeId", bold)
    metadata_sheet.write(0, 3, "SymbolTypeId", bold)
    metadata_sheet.write(0, 4, "IsScheduleOverride", bold)
    metadata_sheet.write(0, 5, "IsTypeParameter", bold)

    for col_idx, param in enumerate(valid_params):
        postfix = ""
        header_format = bold

        if param.storagetype == DB.StorageType.ElementId:
            header_format = workbook.add_format(
                {"bold": True, "bg_color": COLOR_HEADER_ELEMENTID}
            )
        if param.isreadonly:
            header_format = workbook.add_format(
                {"bold": True, "bg_color": COLOR_HEADER_READONLY}
            )
        if _is_reference_only_param(param):
            header_format = workbook.add_format(
                {"bold": True, "bg_color": COLOR_HEADER_REFERENCE}
            )

        forge_type_id = param.forge_type_id
        unit_type_id = None
        symbol_type_id = None
        is_schedule_override = param.name in field_mapping

        if forge_type_id and measurable(forge_type_id):
            field = field_mapping.get(param.name)

            fmt_opts = None
            if field:
                try:
                    field_fmt = field.GetFormatOptions()
                    if field_fmt and not field_fmt.UseDefault:
                        fmt_opts = field_fmt
                except Exception:
                    pass

            if not fmt_opts:
                fmt_opts = project_units.GetFormatOptions(forge_type_id)

            if not fmt_opts.UseDefault:
                unit_type_id = fmt_opts.GetUnitTypeId()
                symbol_type_id = fmt_opts.GetSymbolTypeId()

                if not symbol_type_id.Empty():
                    try:
                        symbol = DB.LabelUtils.GetLabelForSymbol(
                            symbol_type_id
                        )
                        postfix = " [" + symbol + "]"
                    except Exception:
                        try:
                            symbol = DB.LabelUtils.GetLabelForUnit(
                                unit_type_id
                            )
                            postfix = " [" + symbol + "]"
                        except Exception:
                            pass

        worksheet.write(0, col_idx + 1, param.name + postfix, header_format)

        metadata_sheet.write(col_idx + 1, 0, param.name)
        metadata_sheet.write(
            col_idx + 1, 1, forge_type_id.TypeId if forge_type_id else ""
        )
        metadata_sheet.write(
            col_idx + 1, 2, unit_type_id.TypeId if unit_type_id else ""
        )
        metadata_sheet.write(
            col_idx + 1, 3, symbol_type_id.TypeId if symbol_type_id else ""
        )
        metadata_sheet.write(
            col_idx + 1, 4, "Yes" if is_schedule_override else "No"
        )
        metadata_sheet.write(col_idx + 1, 5, "Yes" if param.istype else "No")

    type_col = len(valid_params) + 1
    rowid_col = type_col + 1
    worksheet.write(0, type_col, "_EBIM_TypeId", bold)
    worksheet.write(0, rowid_col, "_EBIM_RowId", bold)

    max_widths = [len("ElementId")] + [len(p.name) for p in valid_params]
    type_cache = {}

    for row_idx, el in enumerate(src_elements, start=1):
        element_id_text = str(get_elementid_value(el.Id))
        worksheet.write(row_idx, 0, element_id_text, locked)
        worksheet.write_string(row_idx, type_col, _type_key_text(el), locked)
        # Untouched copy of the id so an edited column A can be spotted.
        worksheet.write_string(row_idx, rowid_col, element_id_text, locked)

        for col_idx, param in enumerate(valid_params):
            param_name = param.name
            param_el = _lookup_row_param(doc, el, param, type_cache)
            val = "<does not exist>"
            cell_format = locked
            if param_el:
                val = ""
                if param_el.HasValue:
                    try:
                        if param_el.StorageType == DB.StorageType.Double:
                            forge_type_id = param.forge_type_id
                            field = field_mapping.get(param_name)

                            if forge_type_id and measurable(forge_type_id):
                                val, _, _, _ = get_unit_label_and_value(
                                    param_el, forge_type_id, field,
                                    project_units
                                )
                            else:
                                val = param_el.AsDouble()
                        elif param_el.StorageType == DB.StorageType.String:
                            val = param_el.AsString()
                        elif param_el.StorageType == DB.StorageType.Integer:
                            if get_elementid_value(param_el.Id) == \
                                    int(BIP.ELEM_PARTITION_PARAM):
                                val = str(
                                    revit.query.get_element_workset(el).Name
                                )
                            elif is_yesno_parameter(param.definition):
                                val = "Yes" if param_el.AsInteger() else "No"
                            else:
                                val = str(param_el.AsInteger())
                        elif param_el.StorageType == DB.StorageType.ElementId:
                            val = param_el.AsValueString()
                        else:
                            val = "<unsupported>"
                    except Exception as e:
                        LOGGER.warning(
                            "Error reading param '{}' from element {}: {}"
                            .format(param_name, el.Id, e)
                        )
                        val = "<Error>"

                if not param.isreadonly \
                        and not _is_reference_only_param(param):
                    cell_format = _editable_format(param)

            worksheet.write(row_idx, col_idx + 1, val, cell_format)
            if len(str(val)) > max_widths[col_idx + 1]:
                max_widths[col_idx + 1] = min(len(str(val)), 50)

    dropdown_sources = {}
    for col_idx, param in enumerate(valid_params):
        source = create_dropdown_validation(
            doc,
            worksheet,
            workbook,
            col_idx + 1,
            param,
            src_elements,
            hidden_sheets,
        )
        if source:
            dropdown_sources[param.name] = source

    if src_elements:
        _add_type_conflict_formatting(
            workbook, worksheet, valid_params, type_col, len(src_elements)
        )
        _add_edited_id_formatting(
            workbook, worksheet, len(src_elements), rowid_col
        )
        _add_validation_formatting(
            workbook, worksheet, valid_params, len(src_elements),
            dropdown_sources
        )

    _write_legend_sheet(workbook, legend_sheet)

    for col_idx, width in enumerate(max_widths):
        worksheet.set_column(col_idx, col_idx, width + 3)
    worksheet.set_column(type_col, type_col, None, locked, {"hidden": True})
    worksheet.set_column(rowid_col, rowid_col, None, locked, {"hidden": True})

    worksheet.autofilter(0, 0, len(src_elements), rowid_col)
    worksheet.protect(
        "",
        {
            "autofilter": True,
            "sort": True,
            "format_cells": True,
            "format_columns": True,
            "format_rows": True,
            "delete_columns": True,
            "delete_rows": True,
            "select_locked_cells": True,
            "select_unlocked_cells": True,
        },
    )


def export_schedule_to_xlsx(doc, schedule, ordered_elements, file_path):
    """Write one workbook for the schedule; returns the written row count."""
    if not XLSXWRITER_AVAILABLE:
        raise Exception("The 'xlsxwriter' module is not available.")

    src_elements = []
    for el in ordered_elements or []:
        if not el.Category or el.Category.IsTagCategory:
            continue
        src_elements.append(el)

    param_defs, field_mapping = collect_schedule_param_defs(
        schedule, src_elements
    )

    valid_params = []
    for param in param_defs:
        if unit_postfix_pattern.search(param.name):
            LOGGER.warning(
                "Dropping {} from export. Parameter name already contains "
                "brackets.".format(param.name)
            )
            continue
        valid_params.append(param)

    if not valid_params:
        raise ValueError("No valid parameters found in schedule")

    project_units = doc.GetUnits()
    workbook = xlsxwriter.Workbook(file_path)
    has_vba = vba_available() and file_path.lower().endswith(".xlsm")
    if has_vba:
        try:
            workbook.add_vba_project(VBA_BIN_PATH)
            workbook.set_vba_name("ThisWorkbook")
        except Exception as vba_error:
            LOGGER.warning("Could not attach VBA project: %s", vba_error)
            has_vba = False
    body_error = None
    try:
        _write_workbook(
            doc, src_elements, valid_params, field_mapping,
            workbook, project_units, has_vba=has_vba
        )
    except Exception as write_error:
        body_error = write_error
    try:
        workbook.close()
    except Exception as close_error:
        if body_error is None:
            raise Exception(
                "Could not write '{}'. Close it in Excel and retry. "
                "({})".format(file_path, close_error)
            )
        LOGGER.warning("Could not close workbook: %s", close_error)
    if body_error is not None:
        raise body_error

    LOGGER.info(
        "Exported {} elements to {}".format(len(src_elements), file_path)
    )
    return len(src_elements)
