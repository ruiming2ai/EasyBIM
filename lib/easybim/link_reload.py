# -*- coding: utf-8 -*-
"""Shared helpers for asking users to reload loaded linked references."""


RELOAD_PROMPT = (
    "Reload All the loaded Links (Revit Links, Linked CAD, and others)? "
    "Unloaded elements are skipped."
)


def _get_default_logger():
    try:
        from pyrevit import script
        return script.get_logger()
    except Exception:
        return None


def _get_default_revit_modules():
    from pyrevit import DB
    from pyrevit import framework
    from pyrevit import revit
    return DB, framework, revit


def _get_default_elementid_value_func():
    try:
        from pyrevit.compat import get_elementid_value_func
        return get_elementid_value_func()
    except Exception:
        return lambda element_id: int(element_id)


def _log_warning(logger, message, *args):
    if logger:
        try:
            logger.warning(message, *args)
        except Exception:
            pass


def _log_debug(logger, message, *args):
    if logger:
        try:
            logger.debug(message, *args)
        except Exception:
            pass


def _log_info(logger, message, *args):
    if logger:
        try:
            logger.info(message, *args)
        except Exception:
            pass


def _add_unique_link_element(link_elem, link_elems, seen_ids,
                             get_elementid_value):
    if not link_elem:
        return
    try:
        elem_id = int(get_elementid_value(link_elem.Id))
    except Exception:
        return
    if elem_id in seen_ids:
        return
    seen_ids.add(elem_id)
    link_elems.append(link_elem)


def collect_manage_links_elements(doc, DB=None, framework=None,
                                  get_elementid_value=None):
    """Collect link-like type elements shown under Revit Manage Links."""
    if DB is None or framework is None:
        DB, framework, _ = _get_default_revit_modules()
    get_elementid_value = get_elementid_value \
        or _get_default_elementid_value_func()

    link_elems = []
    seen_ids = set()

    for cls_name in ("RevitLinkType", "CADLinkType", "PointCloudType", "ImageType"):
        link_cls = getattr(DB, cls_name, None)
        if not link_cls:
            continue
        try:
            elements = DB.FilteredElementCollector(doc)\
                         .OfClass(framework.get_type(link_cls))\
                         .WhereElementIsElementType()\
                         .ToElements()
        except Exception:
            elements = []
        for element in elements:
            _add_unique_link_element(
                element,
                link_elems,
                seen_ids,
                get_elementid_value
            )

    try:
        ext_ids = DB.ExternalFileUtils.GetAllExternalFileReferences(doc)
    except Exception:
        ext_ids = []

    for ext_id in ext_ids:
        try:
            element = doc.GetElement(ext_id)
        except Exception:
            element = None
        _add_unique_link_element(
            element,
            link_elems,
            seen_ids,
            get_elementid_value
        )

    return link_elems


def get_external_file_reference(doc, link_elem, DB=None):
    if DB is None:
        DB, _, _ = _get_default_revit_modules()

    ext_ref = None
    if hasattr(DB.ExternalFileUtils, "GetExternalFileReference"):
        try:
            ext_ref = DB.ExternalFileUtils.GetExternalFileReference(
                doc,
                link_elem.Id
            )
        except Exception:
            ext_ref = None

    if ext_ref is None and hasattr(link_elem, "GetExternalFileReference"):
        try:
            ext_ref = link_elem.GetExternalFileReference()
        except Exception:
            ext_ref = None

    return ext_ref


def get_linked_cad_type_ids(doc, DB=None, framework=None,
                            get_elementid_value=None):
    if DB is None or framework is None:
        DB, framework, _ = _get_default_revit_modules()
    get_elementid_value = get_elementid_value \
        or _get_default_elementid_value_func()

    linked_type_ids = set()
    import_inst_cls = getattr(DB, "ImportInstance", None)
    if not import_inst_cls:
        return linked_type_ids

    try:
        import_instances = DB.FilteredElementCollector(doc)\
                             .OfClass(framework.get_type(import_inst_cls))\
                             .WhereElementIsNotElementType()\
                             .ToElements()
    except Exception:
        import_instances = []

    for inst in import_instances:
        try:
            if getattr(inst, "IsLinked", False):
                type_id = get_elementid_value(inst.GetTypeId())
                if type_id is not None:
                    linked_type_ids.add(int(type_id))
        except Exception:
            continue
    return linked_type_ids


def is_linked_manage_link_element(doc, link_elem, linked_cad_type_ids,
                                  DB=None, get_elementid_value=None):
    if DB is None:
        DB, _, _ = _get_default_revit_modules()
    get_elementid_value = get_elementid_value \
        or _get_default_elementid_value_func()

    try:
        if isinstance(link_elem, DB.RevitLinkType):
            return True
    except Exception:
        pass

    cad_link_type = getattr(DB, "CADLinkType", None)
    try:
        if cad_link_type and isinstance(link_elem, cad_link_type):
            try:
                if hasattr(link_elem, "IsLink"):
                    return bool(link_elem.IsLink)
            except Exception:
                pass
            try:
                return int(get_elementid_value(link_elem.Id)) in linked_cad_type_ids
            except Exception:
                return False
    except Exception:
        pass

    return get_external_file_reference(doc, link_elem, DB=DB) is not None


def is_unloaded_link_element(doc, link_elem, DB=None):
    if DB is None:
        DB, _, _ = _get_default_revit_modules()

    try:
        if isinstance(link_elem, DB.RevitLinkType):
            try:
                return not DB.RevitLinkType.IsLoaded(doc, link_elem.Id)
            except Exception:
                pass
    except Exception:
        pass

    try:
        cad_link_type = getattr(DB, "CADLinkType", None)
        if cad_link_type and isinstance(link_elem, cad_link_type) \
                and hasattr(cad_link_type, "IsLoaded"):
            try:
                return not cad_link_type.IsLoaded(doc, link_elem.Id)
            except Exception:
                pass
    except Exception:
        pass

    ext_ref = get_external_file_reference(doc, link_elem, DB=DB)

    if ext_ref is not None and hasattr(ext_ref, "GetLinkedFileStatus"):
        try:
            status = str(ext_ref.GetLinkedFileStatus() or "").lower()
            if "unloaded" in status:
                return True
        except Exception:
            pass

    return False


def _element_display_name(link_elem):
    name = getattr(link_elem, "Name", None)
    if name:
        return str(name)
    return type(link_elem).__name__


def reload_link_element(doc, link_elem, revit=None, logger=None,
                        get_elementid_value=None):
    if revit is None:
        _, _, revit = _get_default_revit_modules()
    logger = logger or _get_default_logger()
    get_elementid_value = get_elementid_value \
        or _get_default_elementid_value_func()

    if not hasattr(link_elem, "Reload"):
        return (False, "No Reload method")

    # Link types (RevitLinkType, CADLinkType, etc.) manage their own
    # transactional state and must NOT be wrapped in a Transaction.
    try:
        link_elem.Reload()
        return (True, None)
    except TypeError:
        pass
    except Exception:
        pass

    # Table types (KeynoteTable, AssemblyCodeTable) need a Transaction
    # and a BasicFileLocations argument (None uses the current path).
    try:
        with revit.Transaction("Reload Manage Link", doc=doc):
            link_elem.Reload(None)
        return (True, None)
    except Exception as reload_err:
        msg = str(reload_err)
        _log_warning(
            logger,
            "Failed to reload link element id %s (%s): %s",
            get_elementid_value(link_elem.Id),
            type(link_elem),
            reload_err
        )
        return (False, msg)


def reload_link_elements(doc, link_elements, linked_cad_type_ids,
                         is_linked_func=None, is_unloaded_func=None,
                         reload_func=None, logger=None):
    """Reload eligible link elements and return summary counts + items."""
    is_linked_func = is_linked_func or is_linked_manage_link_element
    is_unloaded_func = is_unloaded_func or is_unloaded_link_element
    reload_func = reload_func or reload_link_element

    summary = {
        "reloaded": 0,
        "skipped_unloaded": 0,
        "skipped_imported": 0,
        "skipped_unsupported": 0,
        "items": [],
    }

    for link_elem in link_elements or []:
        name = _element_display_name(link_elem)
        elem_type = type(link_elem).__name__
        if not is_linked_func(doc, link_elem, linked_cad_type_ids):
            summary["skipped_imported"] += 1
            continue
        if is_unloaded_func(doc, link_elem):
            summary["skipped_unloaded"] += 1
            summary["items"].append({
                "name": name, "element_type": elem_type,
                "status": "Skipped (Unloaded)", "error": "",
            })
            continue
        result = reload_func(doc, link_elem)
        if isinstance(result, tuple):
            success, error_msg = result
        else:
            success = bool(result)
            error_msg = None
        if success:
            summary["reloaded"] += 1
            summary["items"].append({
                "name": name, "element_type": elem_type,
                "status": "Reloaded", "error": "",
            })
        else:
            summary["skipped_unsupported"] += 1
            summary["items"].append({
                "name": name, "element_type": elem_type,
                "status": "Error", "error": error_msg or "Unknown error",
            })

    return summary


def reload_loaded_manage_links(doc, DB=None, framework=None, revit=None,
                               logger=None, get_elementid_value=None):
    if not doc:
        return {
            "reloaded": 0,
            "skipped_unloaded": 0,
            "skipped_imported": 0,
            "skipped_unsupported": 0,
        }

    if DB is None or framework is None or revit is None:
        DB, framework, revit = _get_default_revit_modules()
    logger = logger or _get_default_logger()
    get_elementid_value = get_elementid_value \
        or _get_default_elementid_value_func()

    all_links = collect_manage_links_elements(
        doc,
        DB=DB,
        framework=framework,
        get_elementid_value=get_elementid_value
    )
    _log_debug(logger, "Manage Links candidates found: %s", len(all_links))

    linked_cad_type_ids = get_linked_cad_type_ids(
        doc,
        DB=DB,
        framework=framework,
        get_elementid_value=get_elementid_value
    )
    summary = reload_link_elements(
        doc,
        all_links,
        linked_cad_type_ids,
        is_linked_func=lambda cur_doc, elem, linked_ids:
            is_linked_manage_link_element(
                cur_doc,
                elem,
                linked_ids,
                DB=DB,
                get_elementid_value=get_elementid_value
            ),
        is_unloaded_func=lambda cur_doc, elem:
            is_unloaded_link_element(cur_doc, elem, DB=DB),
        reload_func=lambda cur_doc, elem:
            reload_link_element(
                cur_doc,
                elem,
                revit=revit,
                logger=logger,
                get_elementid_value=get_elementid_value
            ),
        logger=logger
    )

    _log_info(
        logger,
        "Reload links summary | reloaded: %s | skipped imported/non-linked: %s | skipped unloaded: %s | skipped unsupported/failed: %s",
        summary["reloaded"],
        summary["skipped_imported"],
        summary["skipped_unloaded"],
        summary["skipped_unsupported"]
    )
    return summary


def _confirm_reload_with_winforms(title):
    import clr

    clr.AddReference("System.Windows.Forms")
    import System.Windows.Forms as WinForms

    res = WinForms.MessageBox.Show(
        RELOAD_PROMPT,
        title,
        WinForms.MessageBoxButtons.YesNo,
        WinForms.MessageBoxIcon.Question
    )
    return res == WinForms.DialogResult.Yes


def ask_and_reload_loaded_links(doc, title="Print Sheets",
                                confirm_func=None, reload_func=None):
    confirm_func = confirm_func or _confirm_reload_with_winforms
    reload_func = reload_func or reload_loaded_manage_links

    if confirm_func(title):
        return reload_func(doc)
    return None
