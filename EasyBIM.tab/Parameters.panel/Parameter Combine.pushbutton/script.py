# -*- coding: utf-8 -*-
"""Combine selected source parameter values into selected text targets."""

# pylint: disable=import-error,invalid-name,broad-except,too-many-instance-attributes
from collections import defaultdict

import clr

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit
from pyrevit import script
from pyrevit.compat import get_elementid_value_func
from pyrevit.framework import System

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import Thickness
from System.Windows.Controls import Border, TextBlock
from System.Windows.Documents import (
    FlowDocument,
    InlineUIContainer,
    Paragraph,
    Run,
    TextRange,
)
from System.Windows.Input import Cursors, Key
from System.Windows.Media import Brushes


logger = script.get_logger()
get_elementid_value = get_elementid_value_func()
ALL_TOKEN = "<All>"
TEXT_STORAGE_TOKEN = "string"
TEXT_KEYWORDS = ("text", "multiline", "multi-line")
CHIP_TAG_PREFIX = "PC_CHIP::"


def _eid_int(element_id):
    if not element_id:
        return None
    try:
        return int(get_elementid_value(element_id))
    except Exception:
        return None


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _origin_from_parameter(param, pid_int):
    try:
        if getattr(param, "IsShared", False):
            return "Shared"
    except Exception:
        pass
    if pid_int is not None and pid_int < 0:
        return "Built-in"
    return "Project"


def _data_type_from_parameter(param):
    """Return (key, display_label, discipline_label) across API versions."""
    definition = getattr(param, "Definition", None)
    storage_label = _safe_text(getattr(param, "StorageType", "Unknown"))

    data_key = storage_label
    data_label = storage_label
    discipline_label = "Other"

    try:
        spec_id = definition.GetDataType() if definition else None
        spec_typeid = _safe_text(getattr(spec_id, "TypeId", "")) if spec_id else ""
        if spec_typeid:
            data_key = spec_typeid
            try:
                data_label = DB.LabelUtils.GetLabelForSpec(spec_id)
            except Exception:
                data_label = spec_typeid.split(":")[-1] or spec_typeid
            try:
                discipline_id = DB.UnitUtils.GetDiscipline(spec_id)
                try:
                    discipline_label = DB.LabelUtils.GetLabelForDiscipline(discipline_id)
                except Exception:
                    discipline_label = (
                        _safe_text(getattr(discipline_id, "TypeId", "")).split(":")[-1]
                        or _safe_text(discipline_id)
                    )
            except Exception:
                discipline_label = "Other"
            return data_key, data_label, discipline_label
    except Exception:
        pass

    try:
        ptype = definition.ParameterType if definition else None
        ptype_text = _safe_text(ptype)
        if ptype_text:
            data_key = ptype_text
            data_label = ptype_text
    except Exception:
        pass

    return data_key, data_label, discipline_label


def _is_param_match(param, desc):
    if not param or not getattr(param, "Definition", None):
        return False

    pid_int = _eid_int(param.Id)
    if pid_int == desc.param_id_int:
        return True

    if _safe_text(param.Definition.Name) != desc.name:
        return False

    if _safe_text(param.StorageType) != desc.storage_type:
        return False

    data_key, _, _ = _data_type_from_parameter(param)
    return data_key == desc.data_key


def _param_element_id(param_id_int):
    try:
        return DB.ElementId(int(param_id_int))
    except Exception:
        # Revit 2026 removed ElementId(Int32); retry with Int64.
        return DB.ElementId(System.Int64(int(param_id_int)))


def _get_param_by_descriptor(element, desc):
    if not element:
        return None

    param = None
    try:
        if desc.param_id_int < 0:
            bip = System.Enum.ToObject(DB.BuiltInParameter, desc.param_id_int)
            param = element.get_Parameter(bip)
        else:
            param = element.get_Parameter(_param_element_id(desc.param_id_int))
    except Exception:
        param = None

    if _is_param_match(param, desc):
        return param

    try:
        for candidate in element.Parameters:
            if _is_param_match(candidate, desc):
                return candidate
    except Exception:
        pass

    return None


def _source_has_value(source_param):
    try:
        return bool(source_param.HasValue)
    except Exception:
        if source_param.StorageType == DB.StorageType.String:
            return bool(source_param.AsString())
        return True


def _param_value_as_text(param):
    try:
        storage = param.StorageType
    except Exception:
        return ""

    if storage == DB.StorageType.String:
        return param.AsString() or ""

    try:
        value_string = param.AsValueString()
        if value_string:
            return _safe_text(value_string)
    except Exception:
        pass

    if storage == DB.StorageType.Integer:
        try:
            return str(param.AsInteger())
        except Exception:
            return ""

    if storage == DB.StorageType.Double:
        try:
            return str(param.AsDouble())
        except Exception:
            return ""

    if storage == DB.StorageType.ElementId:
        try:
            ref_id = param.AsElementId()
            ref_id_int = _eid_int(ref_id)
            if ref_id_int in (None, -1):
                return ""
            ref_elem = revit.doc.GetElement(ref_id)
            if ref_elem:
                ref_name = _safe_text(getattr(ref_elem, "Name", ""))
                if ref_name:
                    return ref_name
            return str(ref_id_int)
        except Exception:
            return ""

    return ""


def _is_text_like_target(desc):
    storage_text = _safe_text(desc.storage_type).lower()
    if TEXT_STORAGE_TOKEN in storage_text:
        return True

    data_label = _safe_text(desc.data_label).lower()
    data_key = _safe_text(desc.data_key).lower()
    return any(k in data_label or k in data_key for k in TEXT_KEYWORDS)


def _collect_scope_elements():
    doc = revit.doc
    uidoc = revit.uidoc

    selected_ids = []
    try:
        selected_ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        selected_ids = []

    selected_elements = [doc.GetElement(x) for x in selected_ids if doc.GetElement(x)]
    selected_instances = []
    selected_types = []

    for elem in selected_elements:
        if isinstance(elem, DB.ElementType):
            selected_types.append(elem)
        else:
            selected_instances.append(elem)

    if selected_instances or selected_types:
        scope_note = "Scope: {} selected element(s)".format(len(selected_elements))
        instance_elements = list(selected_instances)
        type_map = {_eid_int(t.Id): t for t in selected_types if t}
        for inst in selected_instances:
            try:
                type_id = inst.GetTypeId()
                if type_id and _eid_int(type_id) not in (-1, None):
                    t_elem = doc.GetElement(type_id)
                    if t_elem and isinstance(t_elem, DB.ElementType):
                        type_map[_eid_int(type_id)] = t_elem
            except Exception:
                pass
        return instance_elements, list(type_map.values()), scope_note

    use_all = forms.alert(
        "No elements selected.\nUse all model elements in the active document?",
        title="Parameter Combine",
        yes=True,
        no=True,
    )
    if not use_all:
        return [], [], "Scope: canceled"

    all_instances = []
    collector = DB.FilteredElementCollector(doc).WhereElementIsNotElementType()
    for elem in collector:
        try:
            if elem is None:
                continue
            if not getattr(elem, "Category", None):
                continue
            all_instances.append(elem)
        except Exception:
            continue

    type_map = {}
    for inst in all_instances:
        try:
            type_id = inst.GetTypeId()
            type_id_int = _eid_int(type_id)
            if type_id_int in (None, -1):
                continue
            if type_id_int in type_map:
                continue
            t_elem = doc.GetElement(type_id)
            if t_elem and isinstance(t_elem, DB.ElementType):
                type_map[type_id_int] = t_elem
        except Exception:
            continue

    scope_note = "Scope: all model instances ({})".format(len(all_instances))
    return all_instances, list(type_map.values()), scope_note


class ParamDescriptor(object):
    def __init__(
        self,
        param_id_int,
        name,
        is_instance,
        origin,
        storage_type,
        data_key,
        data_label,
        discipline_label,
        has_writable=False,
    ):
        self.param_id_int = param_id_int
        self.name = name
        self.is_instance = is_instance
        self.origin = origin
        self.storage_type = storage_type
        self.data_key = data_key
        self.data_label = data_label
        self.discipline_label = discipline_label
        self.has_writable = has_writable
        self.key = "{}|{}".format("I" if is_instance else "T", param_id_int)
        self.sort_key = (
            name.lower(),
            0 if is_instance else 1,
            data_label.lower(),
            origin.lower(),
            param_id_int,
        )

    @property
    def kind_label(self):
        return "Instance" if self.is_instance else "Type"

    @property
    def display(self):
        return "{} [{} | {} | {}]".format(
            self.name,
            self.kind_label,
            self.data_label,
            self.origin,
        )

    def __repr__(self):
        return self.display


def _collect_parameter_catalog(instance_elements, type_elements):
    catalog = {}

    def scan_element_parameters(element, is_instance):
        try:
            for param in element.Parameters:
                definition = getattr(param, "Definition", None)
                if not definition:
                    continue
                name = _safe_text(definition.Name).strip()
                if not name:
                    continue

                param_id_int = _eid_int(param.Id)
                if param_id_int is None:
                    continue

                storage_type = _safe_text(param.StorageType)
                data_key, data_label, discipline_label = _data_type_from_parameter(param)
                origin = _origin_from_parameter(param, param_id_int)
                descriptor_key = "{}|{}".format("I" if is_instance else "T", param_id_int)

                if descriptor_key in catalog:
                    desc = catalog[descriptor_key]
                    if not param.IsReadOnly:
                        desc.has_writable = True
                    continue

                desc = ParamDescriptor(
                    param_id_int=param_id_int,
                    name=name,
                    is_instance=is_instance,
                    origin=origin,
                    storage_type=storage_type,
                    data_key=data_key,
                    data_label=data_label,
                    discipline_label=discipline_label,
                    has_writable=(not param.IsReadOnly),
                )
                catalog[descriptor_key] = desc
        except Exception:
            return

    for inst in instance_elements:
        scan_element_parameters(inst, is_instance=True)
    for typ in type_elements:
        scan_element_parameters(typ, is_instance=False)

    return sorted(catalog.values(), key=lambda x: x.sort_key)


def _dedupe_preserve_order(values):
    seen = set()
    output = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


class ParameterCombineWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.is_ready = False

        self.instance_elements, self.type_elements, scope_note = _collect_scope_elements()
        if not (self.instance_elements or self.type_elements):
            return

        self.all_descriptors = _collect_parameter_catalog(
            self.instance_elements, self.type_elements
        )
        if not self.all_descriptors:
            forms.alert(
                "No parameters found in selected scope.",
                title="Parameter Combine",
            )
            return

        self.source_descriptors = list(self.all_descriptors)
        self.target_descriptors = [
            x for x in self.all_descriptors if x.has_writable and _is_text_like_target(x)
        ]
        if not self.target_descriptors:
            forms.alert(
                "No writable text or multi-line text target parameters found in selected scope.",
                title="Parameter Combine",
            )
            return

        self.source_desc_by_key = {x.key: x for x in self.source_descriptors}
        self._chip_counter = 0
        self._chip_registry = {}
        self._selected_chip_id = None

        self.scope_tb.Text = scope_note
        self.status_tb.Text = "Loaded {} source / {} text-target parameters".format(
            len(self.source_descriptors),
            len(self.target_descriptors),
        )

        self._bind_filter_options()
        self._refresh_source_list()
        self._refresh_target_list()
        self._ensure_format_document()
        self._update_token_help()
        self.is_ready = True

    def _ensure_format_document(self):
        if not getattr(self.combine_format_rtb, "Document", None):
            doc = FlowDocument()
            doc.Blocks.Add(Paragraph())
            self.combine_format_rtb.Document = doc
        elif self.combine_format_rtb.Document.Blocks.Count == 0:
            self.combine_format_rtb.Document.Blocks.Add(Paragraph())

    def _bind_filter_options(self):
        source_ptypes = [ALL_TOKEN] + sorted({x.origin for x in self.source_descriptors})
        source_dtypes = [ALL_TOKEN] + sorted({x.data_label for x in self.source_descriptors})
        source_discs = [ALL_TOKEN] + sorted({x.discipline_label for x in self.source_descriptors})

        target_ptypes = [ALL_TOKEN] + sorted({x.origin for x in self.target_descriptors})
        target_dtypes = [ALL_TOKEN] + sorted({x.data_label for x in self.target_descriptors})
        target_discs = [ALL_TOKEN] + sorted({x.discipline_label for x in self.target_descriptors})

        self.source_ptype_cb.ItemsSource = source_ptypes
        self.source_dtype_cb.ItemsSource = source_dtypes
        self.source_disc_cb.ItemsSource = source_discs
        self.target_ptype_cb.ItemsSource = target_ptypes
        self.target_dtype_cb.ItemsSource = target_dtypes
        self.target_disc_cb.ItemsSource = target_discs

        self.source_ptype_cb.SelectedIndex = 0
        self.source_dtype_cb.SelectedIndex = 0
        self.source_disc_cb.SelectedIndex = 0
        self.target_ptype_cb.SelectedIndex = 0
        self.target_dtype_cb.SelectedIndex = 0
        self.target_disc_cb.SelectedIndex = 0

        self.source_type_cb.IsChecked = True
        self.source_inst_cb.IsChecked = True
        self.target_type_cb.IsChecked = True
        self.target_inst_cb.IsChecked = True

    def _apply_filters(
        self,
        descriptors,
        search_text,
        ptype_value,
        dtype_value,
        disc_value,
        allow_type,
        allow_instance,
    ):
        search_token = _safe_text(search_text).strip().lower()
        ptype_value = _safe_text(ptype_value)
        dtype_value = _safe_text(dtype_value)
        disc_value = _safe_text(disc_value)

        allow_type = bool(allow_type)
        allow_instance = bool(allow_instance)
        if not allow_type and not allow_instance:
            return []

        filtered = []
        for item in descriptors:
            if search_token and search_token not in item.name.lower():
                continue
            if ptype_value and ptype_value != ALL_TOKEN and item.origin != ptype_value:
                continue
            if dtype_value and dtype_value != ALL_TOKEN and item.data_label != dtype_value:
                continue
            if disc_value and disc_value != ALL_TOKEN and item.discipline_label != disc_value:
                continue
            if item.is_instance and not allow_instance:
                continue
            if (not item.is_instance) and not allow_type:
                continue
            filtered.append(item)
        return filtered

    def _refresh_source_list(self):
        current_selected_keys = set()
        try:
            for item in self.source_lb.SelectedItems:
                current_selected_keys.add(item.key)
        except Exception:
            current_selected_keys = set()

        filtered = self._apply_filters(
            self.source_descriptors,
            self.source_search_tb.Text,
            self.source_ptype_cb.SelectedItem,
            self.source_dtype_cb.SelectedItem,
            self.source_disc_cb.SelectedItem,
            self.source_type_cb.IsChecked,
            self.source_inst_cb.IsChecked,
        )
        self.source_lb.ItemsSource = filtered

        # Restore previous multi-selection.
        for item in filtered:
            if item.key in current_selected_keys:
                try:
                    self.source_lb.SelectedItems.Add(item)
                except Exception:
                    continue

        self.source_count_tb.Text = "{} item(s)".format(len(filtered))
        self._update_token_help()

    def _refresh_target_list(self):
        current_selected_keys = set()
        try:
            for item in self.target_lb.SelectedItems:
                current_selected_keys.add(item.key)
        except Exception:
            current_selected_keys = set()

        filtered = self._apply_filters(
            self.target_descriptors,
            self.target_search_tb.Text,
            self.target_ptype_cb.SelectedItem,
            self.target_dtype_cb.SelectedItem,
            self.target_disc_cb.SelectedItem,
            self.target_type_cb.IsChecked,
            self.target_inst_cb.IsChecked,
        )
        self.target_lb.ItemsSource = filtered

        for item in filtered:
            if item.key in current_selected_keys:
                try:
                    self.target_lb.SelectedItems.Add(item)
                except Exception:
                    continue

        self.target_count_tb.Text = "{} item(s)".format(len(filtered))

    def _get_selected_sources(self):
        try:
            return [x for x in self.source_lb.SelectedItems]
        except Exception:
            return []

    def _get_selected_targets(self):
        try:
            return [x for x in self.target_lb.SelectedItems]
        except Exception:
            return []

    def _update_token_help(self):
        self._prune_chip_registry()
        selected_sources = self._get_selected_sources()
        chip_count = len(self._chip_registry)
        if not selected_sources:
            self.token_help_tb.Text = (
                "Select source parameters and click 'Add Parameters' to insert boxed tokens at cursor. "
                "Use Delete/Backspace to remove selected or adjacent tokens. "
                "Current tokens: {}.".format(chip_count)
            )
            return

        previews = ["[{}]".format(x.name) for x in selected_sources[:8]]
        if len(selected_sources) > 8:
            previews.append("...")
        self.token_help_tb.Text = (
            "Ready to add: {}. Click 'Add Parameters' to insert at cursor. "
            "Current tokens: {}.".format(", ".join(previews), chip_count)
        )

    def _make_chip_id(self):
        self._chip_counter += 1
        return "{}{}".format(CHIP_TAG_PREFIX, self._chip_counter)

    def _create_chip_border(self, desc, chip_id):
        border = Border()
        border.BorderBrush = Brushes.SteelBlue
        border.BorderThickness = Thickness(1)
        border.Background = Brushes.AliceBlue
        border.Padding = Thickness(4, 1, 4, 1)
        border.Margin = Thickness(1, 0, 1, 0)
        border.Tag = chip_id
        border.Focusable = False
        border.Cursor = Cursors.Hand

        textblock = TextBlock()
        textblock.Text = "[{}]".format(desc.name)
        textblock.FontSize = 12
        border.Child = textblock
        border.MouseLeftButtonDown += self._chip_mouse_left_down
        return border

    def _set_selected_chip(self, chip_id):
        self._selected_chip_id = chip_id if chip_id in self._chip_registry else None
        for current_id, chip_data in self._chip_registry.items():
            border = chip_data.get("border")
            if not border:
                continue
            if current_id == self._selected_chip_id:
                border.Background = Brushes.LightYellow
                border.BorderBrush = Brushes.DarkGreen
            else:
                border.Background = Brushes.AliceBlue
                border.BorderBrush = Brushes.SteelBlue

    def _chip_mouse_left_down(self, sender, args):
        chip_id = _safe_text(getattr(sender, "Tag", ""))
        if chip_id in self._chip_registry:
            self._set_selected_chip(chip_id)
            self.combine_format_rtb.Focus()
            self.status_tb.Text = "Selected token '{}'. Press Delete/Backspace to remove.".format(
                self._chip_registry[chip_id]["name"]
            )
        try:
            args.Handled = True
        except Exception:
            pass

    def _prune_chip_registry(self):
        removed_selected = False
        for chip_id in list(self._chip_registry.keys()):
            chip_data = self._chip_registry.get(chip_id)
            inline = chip_data.get("inline") if chip_data else None
            parent = None
            if inline:
                try:
                    parent = inline.Parent
                except Exception:
                    parent = None
            if not parent:
                self._chip_registry.pop(chip_id, None)
                if chip_id == self._selected_chip_id:
                    removed_selected = True

        if removed_selected:
            self._selected_chip_id = None

    def _insert_chip_at_pointer(self, desc, pointer):
        chip_id = self._make_chip_id()
        border = self._create_chip_border(desc, chip_id)
        inline = InlineUIContainer(border, pointer)
        self._chip_registry[chip_id] = {
            "desc_key": desc.key,
            "name": desc.name,
            "is_instance": desc.is_instance,
            "inline": inline,
            "border": border,
        }
        self._set_selected_chip(chip_id)
        return inline

    def _remove_chip(self, chip_id):
        chip_data = self._chip_registry.get(chip_id)
        if not chip_data:
            return False

        inline = chip_data.get("inline")
        parent = None
        if inline:
            try:
                parent = inline.Parent
            except Exception:
                parent = None

        if parent and hasattr(parent, "Inlines"):
            try:
                parent.Inlines.Remove(inline)
            except Exception:
                pass

        self._chip_registry.pop(chip_id, None)
        self._set_selected_chip(None)
        self._update_token_help()
        return True

    def _find_adjacent_chip_id(self, key_value):
        self._prune_chip_registry()
        caret = self.combine_format_rtb.CaretPosition
        if not caret:
            return None

        for chip_id, chip_data in self._chip_registry.items():
            inline = chip_data.get("inline")
            if not inline:
                continue
            try:
                if key_value == Key.Back and caret.CompareTo(inline.ContentEnd) == 0:
                    return chip_id
                if key_value == Key.Delete and caret.CompareTo(inline.ContentStart) == 0:
                    return chip_id
            except Exception:
                continue
        return None

    def _get_format_segments(self):
        self._prune_chip_registry()
        self._ensure_format_document()
        segments = []
        token_desc_keys = []

        blocks = []
        for block in self.combine_format_rtb.Document.Blocks:
            blocks.append(block)

        for block_index, block in enumerate(blocks):
            if isinstance(block, Paragraph):
                inline = block.Inlines.FirstInline
                while inline:
                    if isinstance(inline, Run):
                        text_value = _safe_text(inline.Text)
                        if text_value:
                            segments.append(("text", text_value))
                    elif isinstance(inline, InlineUIContainer):
                        child = getattr(inline, "Child", None)
                        chip_id = _safe_text(getattr(child, "Tag", ""))
                        chip_data = self._chip_registry.get(chip_id)
                        if chip_data:
                            desc_key = chip_data["desc_key"]
                            segments.append(("token", desc_key))
                            token_desc_keys.append(desc_key)
                    inline = inline.NextInline
            else:
                try:
                    block_text = _safe_text(TextRange(block.ContentStart, block.ContentEnd).Text)
                    block_text = block_text.replace("\r", "")
                    if block_text:
                        segments.append(("text", block_text))
                except Exception:
                    pass

            if block_index < len(blocks) - 1:
                segments.append(("text", "\n"))

        return segments, token_desc_keys

    def source_filter_changed(self, sender, args):
        self._refresh_source_list()

    def target_filter_changed(self, sender, args):
        self._refresh_target_list()

    def source_selection_changed(self, sender, args):
        self._update_token_help()
        selected_sources = self._get_selected_sources()
        if not selected_sources:
            self.status_tb.Text = "Select one or more source parameters."
            return
        kinds = sorted(set("Instance" if x.is_instance else "Type" for x in selected_sources))
        self.status_tb.Text = "Selected {} source parameter(s) [{}]".format(
            len(selected_sources), ", ".join(kinds)
        )

    def add_selected_to_format(self, sender, args):
        self._ensure_format_document()
        selected_sources = self._get_selected_sources()
        if not selected_sources:
            forms.alert(
                "Select one or more source parameters first.",
                title="Parameter Combine",
            )
            return

        caret = self.combine_format_rtb.CaretPosition or self.combine_format_rtb.Document.ContentEnd
        for index, desc in enumerate(selected_sources):
            inline = self._insert_chip_at_pointer(desc, caret)
            caret = inline.ElementEnd
            if index < len(selected_sources) - 1:
                spacer = Run(" ", caret)
                caret = spacer.ElementEnd

        self.combine_format_rtb.CaretPosition = caret
        self.combine_format_rtb.Focus()
        self._update_token_help()
        self.status_tb.Text = "Added {} token(s) at cursor position.".format(len(selected_sources))

    def combine_format_preview_keydown(self, sender, args):
        key_value = args.Key
        if key_value not in (Key.Back, Key.Delete):
            return

        chip_id = self._selected_chip_id
        if not chip_id:
            chip_id = self._find_adjacent_chip_id(key_value)

        if chip_id and self._remove_chip(chip_id):
            self.status_tb.Text = "Removed token. You can add it again anytime."
            try:
                args.Handled = True
            except Exception:
                pass

    def cancel_click(self, sender, args):
        self.Close()

    def run_click(self, sender, args):
        target_descs = self._get_selected_targets()
        if not target_descs:
            forms.alert(
                "Please select one or more target parameters.",
                title="Parameter Combine",
            )
            return

        segments, token_desc_keys = self._get_format_segments()
        if not token_desc_keys:
            forms.alert(
                "At least one parameter token is required in Combined Parameter Values Format.",
                title="Parameter Combine",
            )
            return

        unique_token_keys = _dedupe_preserve_order(token_desc_keys)
        resolved_source_descs = [
            self.source_desc_by_key[x]
            for x in unique_token_keys
            if x in self.source_desc_by_key
        ]
        if not resolved_source_descs:
            forms.alert(
                "No valid parameter tokens found in the format.\nPlease add source parameters again.",
                title="Parameter Combine",
            )
            return

        source_kinds = set(x.is_instance for x in resolved_source_descs)
        if len(source_kinds) != 1:
            lines = [
                "Mixed Instance/Type parameter tokens are not allowed in one format.",
                "Token parameters:",
            ]
            for item in resolved_source_descs[:20]:
                lines.append(" - {} [{}]".format(item.name, item.kind_label))
            forms.alert("\n".join(lines), title="Parameter Combine")
            return
        source_is_instance = list(source_kinds)[0]

        invalid_targets = []
        invalid_target_keys = set()
        for tdesc in target_descs:
            if (not _is_text_like_target(tdesc)) or (tdesc.is_instance != source_is_instance):
                if tdesc.key in invalid_target_keys:
                    continue
                invalid_target_keys.add(tdesc.key)
                invalid_targets.append(tdesc)
        if invalid_targets:
            lines = [
                "Target parameters must be text/multi-line text and match source Instance/Type kind.",
                "Invalid targets:",
            ]
            for item in invalid_targets[:20]:
                lines.append(" - {}".format(item.display))
            forms.alert("\n".join(lines), title="Parameter Combine")
            return

        processing_elements = self.instance_elements if source_is_instance else self.type_elements
        if not processing_elements:
            forms.alert(
                "No elements available in scope for selected source parameter kind.",
                title="Parameter Combine",
            )
            return

        stats = defaultdict(int)
        sample_ids = defaultdict(set)
        detail_rows = []
        token_desc_lookup = {key: self.source_desc_by_key.get(key) for key in unique_token_keys}
        unresolved_token_keys = [key for key, desc in token_desc_lookup.items() if not desc]
        if unresolved_token_keys:
            detail_rows.append(
                "WARNING: unresolved token key(s): {}".format(
                    ", ".join(unresolved_token_keys[:20])
                )
            )

        with revit.Transaction("Parameter Combine"):
            for elem in processing_elements:
                elem_id_int = _eid_int(elem.Id)
                stats["elements_processed"] += 1

                combined_parts = []
                for seg_type, seg_value in segments:
                    if seg_type == "text":
                        combined_parts.append(seg_value)
                        continue

                    source_desc = token_desc_lookup.get(seg_value)
                    if not source_desc:
                        stats["missing_source"] += 1
                        if elem_id_int is not None:
                            sample_ids["missing_source"].add(elem_id_int)
                        combined_parts.append("")
                        continue

                    src_param = _get_param_by_descriptor(elem, source_desc)
                    if not src_param:
                        stats["missing_source"] += 1
                        if elem_id_int is not None:
                            sample_ids["missing_source"].add(elem_id_int)
                        src_text = ""
                    elif not _source_has_value(src_param):
                        stats["empty_source"] += 1
                        if elem_id_int is not None:
                            sample_ids["empty_source"].add(elem_id_int)
                        src_text = ""
                    else:
                        src_text = _param_value_as_text(src_param)

                    combined_parts.append(src_text)

                combined_text = "".join(combined_parts)

                for target_desc in target_descs:
                    target_param = _get_param_by_descriptor(elem, target_desc)
                    if not target_param:
                        stats["missing_target"] += 1
                        if elem_id_int is not None:
                            sample_ids["missing_target"].add(elem_id_int)
                        continue
                    if target_param.IsReadOnly:
                        stats["readonly_target"] += 1
                        if elem_id_int is not None:
                            sample_ids["readonly_target"].add(elem_id_int)
                        continue
                    if target_param.StorageType != DB.StorageType.String:
                        stats["warnings"] += 1
                        if elem_id_int is not None:
                            sample_ids["warnings"].add(elem_id_int)
                        if len(detail_rows) < 30:
                            detail_rows.append(
                                "id {} -> WARNING: target '{}' is not text/multi-line text."
                                .format(elem_id_int, target_desc.name)
                            )
                        continue

                    try:
                        target_param.Set(combined_text)
                        stats["writes"] += 1
                    except Exception as ex:
                        stats["errors"] += 1
                        if elem_id_int is not None:
                            sample_ids["errors"].add(elem_id_int)
                        if len(detail_rows) < 30:
                            detail_rows.append("id {} -> {}".format(elem_id_int, ex))

        summary = [
            "Parameter Combine completed.",
            "Source tokens in format: {}".format(len(unique_token_keys)),
            "Targets: {}".format(len(target_descs)),
            "Elements processed: {}".format(stats["elements_processed"]),
            "Values written: {}".format(stats["writes"]),
            "Missing source: {}".format(stats["missing_source"]),
            "Empty source: {}".format(stats["empty_source"]),
            "Missing target: {}".format(stats["missing_target"]),
            "Read-only target: {}".format(stats["readonly_target"]),
            "Warnings (skipped): {}".format(stats["warnings"]),
            "Errors: {}".format(stats["errors"]),
        ]

        def append_sample(label_key, label):
            if not sample_ids[label_key]:
                return
            ids = sorted(sample_ids[label_key])[:20]
            summary.append("{} sample element ids: {}".format(label, ", ".join(str(x) for x in ids)))

        summary.append("")
        append_sample("missing_source", "Missing source")
        append_sample("empty_source", "Empty source")
        append_sample("missing_target", "Missing target")
        append_sample("readonly_target", "Read-only target")
        append_sample("warnings", "Warnings")
        append_sample("errors", "Errors")

        if detail_rows:
            summary.append("")
            summary.append("Details:")
            summary.extend(detail_rows)

        summary_text = "\n".join(summary)
        self.status_tb.Text = "Done. Wrote {} value(s).".format(stats["writes"])
        self._update_token_help()
        forms.alert(summary_text, title="Parameter Combine", warn_icon=False)


def main():
    window = ParameterCombineWindow("Script.xaml")
    if not window.is_ready:
        return
    window.ShowDialog()


if __name__ == "__main__":
    main()

