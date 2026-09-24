# -*- coding: utf-8 -*-
"""Load, validate, save, import, and export user settings."""

from __future__ import absolute_import

import copy
import datetime
import io
import json
import os
import re
import tempfile

from viewtabcolors import defaults


MAX_SETTINGS_BYTES = 1024 * 1024
COLOR_RE = re.compile(r"^#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")
HEX_DIGITS = "0123456789ABCDEF"
ACTIVE_FILENAME = "settings.json"
LOG_FILENAME = "TabColor.log"


class ConfigError(Exception):
    """Raised when a settings file is malformed or incompatible."""


def rgb_to_hex(red, green, blue):
    """Convert Python or CLR numeric RGB components to ``#RRGGBB``.

    IronPython can pass ``System.Byte`` values from WinForms through
    ``str.format`` without honoring Python's ``02X`` formatter. Building each
    byte from hexadecimal digits avoids that interop behavior entirely.
    """
    components = []
    for value in (red, green, blue):
        try:
            component = int(value)
        except Exception:
            raise ConfigError("RGB color components must be numbers.")
        if component < 0 or component > 255:
            raise ConfigError("RGB color components must be between 0 and 255.")
        components.append(
            HEX_DIGITS[component // 16] + HEX_DIGITS[component % 16]
        )
    return "#" + "".join(components)


def _utc_now_text():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _base_user_dir():
    for env_name in ("APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(env_name)
        if value:
            return value
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        return os.path.join(user_profile, "AppData", "Roaming")
    return tempfile.gettempdir()


def get_config_dir(create=True):
    # Use a fresh namespace for the integration-ready, opt-in build. Reusing
    # ViewTabColors could silently inherit an older enabled=True profile.
    path = os.path.join(_base_user_dir(), "pyRevit", "TabColor")
    if create and not os.path.isdir(path):
        try:
            os.makedirs(path)
        except OSError:
            if not os.path.isdir(path):
                raise
    return path


def get_active_path():
    return os.path.join(get_config_dir(True), ACTIVE_FILENAME)


def get_log_path():
    return os.path.join(get_config_dir(True), LOG_FILENAME)


def log(message):
    """Best-effort diagnostic log. Hooks must never fail because logging did."""
    try:
        line = u"{0}  {1}\n".format(_utc_now_text(), message)
        with io.open(get_log_path(), "a", encoding="utf-8") as stream:
            stream.write(line)
    except Exception:
        pass


def normalize_color(value):
    if value is None:
        raise ConfigError("A color is missing.")
    value = str(value).strip().upper()
    if not COLOR_RE.match(value):
        raise ConfigError(
            "Invalid color '{0}'. Use #RRGGBB or #AARRGGBB.".format(value)
        )
    return value


def _bool_value(value, field_name):
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise ConfigError("'{0}' must be true or false.".format(field_name))


def validate_profile(raw_profile):
    """Validate and normalize a profile without mutating the input."""
    if not isinstance(raw_profile, dict):
        raise ConfigError("The settings root must be a JSON object.")

    schema = raw_profile.get("schema")
    if schema != defaults.SCHEMA:
        raise ConfigError("This is not a Tab Color settings file.")

    if "schema_version" not in raw_profile:
        raise ConfigError("The settings file has no schema_version.")
    version = raw_profile.get("schema_version")
    try:
        version = int(version)
    except Exception:
        raise ConfigError("schema_version must be a number.")
    if version > defaults.SCHEMA_VERSION:
        raise ConfigError(
            "This settings file was created by a newer version (schema {0}).".format(
                version
            )
        )
    if version < 1:
        raise ConfigError("Unsupported settings schema version: {0}.".format(version))

    supplied_rules = raw_profile.get("rules", [])
    if not isinstance(supplied_rules, list):
        raise ConfigError("'rules' must be a JSON array.")

    supplied_by_id = {}
    for item in supplied_rules:
        if not isinstance(item, dict):
            raise ConfigError("Every rule must be a JSON object.")
        rule_id = str(item.get("id", "")).strip()
        if rule_id:
            supplied_by_id[rule_id] = item

    normalized = defaults.make_default_profile()
    normalized["enabled"] = _bool_value(
        raw_profile.get("enabled", normalized["enabled"]), "enabled"
    )

    for rule in normalized["rules"]:
        supplied = supplied_by_id.get(rule["id"])
        if not supplied:
            continue
        rule["enabled"] = _bool_value(
            supplied.get("enabled", rule["enabled"]),
            "rules.{0}.enabled".format(rule["id"]),
        )
        rule["color"] = normalize_color(supplied.get("color", rule["color"]))

    normalized["schema"] = defaults.SCHEMA
    normalized["schema_version"] = defaults.SCHEMA_VERSION
    normalized["modified_utc"] = str(
        raw_profile.get("modified_utc", _utc_now_text())
    )
    return normalized


def _read_json(path):
    if not path or not os.path.isfile(path):
        raise ConfigError("Settings file was not found: {0}".format(path))
    if os.path.getsize(path) > MAX_SETTINGS_BYTES:
        raise ConfigError("Settings file is larger than 1 MB.")
    try:
        with io.open(path, "r", encoding="utf-8-sig") as stream:
            return json.load(stream)
    except ConfigError:
        raise
    except Exception as ex:
        raise ConfigError("Could not read JSON: {0}".format(ex))


def _atomic_write_json(path, data):
    target_dir = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir)

    fd, temporary_path = tempfile.mkstemp(
        prefix=".viewtabcolors-", suffix=".tmp", dir=target_dir
    )
    os.close(fd)
    try:
        text = json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False)
        with io.open(temporary_path, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.write(u"\n")

        if hasattr(os, "replace"):
            os.replace(temporary_path, path)
        else:
            backup_path = path + ".bak"
            if os.path.exists(backup_path):
                os.remove(backup_path)
            if os.path.exists(path):
                os.rename(path, backup_path)
            os.rename(temporary_path, path)
            if os.path.exists(backup_path):
                os.remove(backup_path)
    finally:
        if os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except Exception:
                pass


def load_profile():
    """Load the active profile, falling back safely to defaults."""
    path = get_active_path()
    if not os.path.isfile(path):
        return defaults.make_default_profile()
    try:
        return validate_profile(_read_json(path))
    except Exception as ex:
        log("Active settings rejected; defaults used. {0}".format(ex))
        return defaults.make_default_profile()


def save_profile(profile):
    normalized = validate_profile(copy.deepcopy(profile))
    normalized["modified_utc"] = _utc_now_text()
    _atomic_write_json(get_active_path(), normalized)
    return normalized


def read_profile_file(path):
    return validate_profile(_read_json(path))


def export_profile(profile, path):
    normalized = validate_profile(copy.deepcopy(profile))
    normalized["exported_utc"] = _utc_now_text()
    _atomic_write_json(path, normalized)
    return normalized
