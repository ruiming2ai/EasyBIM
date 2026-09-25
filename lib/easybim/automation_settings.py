# -*- coding: utf-8 -*-
"""Per-user startup automation preferences, shared by all pyRevit engines."""
import io
import json
import os
import tempfile


DEFAULTS = {"workset_enabled": True, "coordination_review_enabled": True}


def settings_path():
    try:
        from pyrevit import script
        return script.get_universal_data_file("EasyBIM_Automation", "json")
    except Exception:
        return None


def load_settings(path=None):
    """Return (settings, error); missing files retain existing enabled behavior."""
    result = dict(DEFAULTS)
    path = path or settings_path()
    if not path or not os.path.isfile(path):
        return result, ""
    try:
        with io.open(path, "r", encoding="utf-8") as stream:
            raw = json.load(stream)
        if not isinstance(raw, dict):
            raise ValueError("Expected an object of automation preferences.")
        invalid = []
        for key in DEFAULTS:
            if key in raw:
                if isinstance(raw[key], bool):
                    result[key] = raw[key]
                else:
                    invalid.append(key)
        if invalid:
            return result, "Invalid automation values: {0}. Defaults are shown.".format(", ".join(invalid))
        return result, ""
    except Exception as ex:
        return result, "Could not read automation settings: {0}".format(ex)


def is_enabled(key):
    return load_settings()[0][key]


def save_settings(settings, path=None):
    """Replace the settings file only after a complete successful write."""
    path = path or settings_path()
    if not path:
        return False, "No automation settings file path is available."
    temporary = None
    try:
        data = {key: settings[key] for key in DEFAULTS}
        if any(not isinstance(value, bool) for value in data.values()):
            raise ValueError("Automation preferences must be enabled or disabled.")
        folder = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(folder):
            os.makedirs(folder)
        fd, temporary = tempfile.mkstemp(prefix="automation-", suffix=".tmp", dir=folder)
        os.close(fd)
        with io.open(temporary, "w", encoding="utf-8") as stream:
            stream.write(u"{0}\n".format(json.dumps(data, indent=2, sort_keys=True)))
        if hasattr(os, "replace"):
            os.replace(temporary, path)
        elif os.name == "nt" and os.path.isfile(path):
            from System.IO import File
            File.Replace(temporary, path, None)
        else:
            os.rename(temporary, path)
        return True, ""
    except Exception as ex:
        return False, "Could not save automation settings: {0}".format(ex)
    finally:
        if temporary and os.path.isfile(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass


def apply_listener_settings(settings, uiapp=None):
    from easybim import coordination_review_passive as passive
    if settings["coordination_review_enabled"]:
        passive.register_passive_detector(uiapp, source="automation-settings")
    else:
        passive.unregister_passive_detector(uiapp, source="automation-settings")
        passive.clear_all_records()
