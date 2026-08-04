# -*- coding: utf-8 -*-
"""Where Tag Align settings are stored, and how the three places are merged.

Three destinations, because they answer three different questions:

``local``   ``%APPDATA%\\pyRevit\\pyRevit_EasyBIM_TagAlign_presets.json``.
            Survives closing the model, closing Revit and upgrading Revit, and
            is there in every model *you* open.  Nobody else sees it.

``model``   An Extensible Storage entity inside the ``.rvt`` itself.  It travels
            with the model, so on an ACC cloud model every teammate who opens it
            gets the presets - after a Sync to Central.  This is the only
            mechanism that reaches a cloud-model team, because no Revit API
            writes files into ACC Docs.  The Revit layer injects the read and
            write callables so this module stays importable without Revit.

``shared``  A JSON file at a path the user sets once - a network drive, or an
            ACC Desktop Connector folder, which Revit sees as an ordinary local
            path.  Reaches teammates immediately and works across projects.

pyRevit's ``get_universal_data_file`` is used rather than ``get_data_file``:
the latter stamps the Revit version into the filename, which would silo a
user's presets per Revit release.
"""

# pylint: disable=broad-except
import io
import json
import os

from tag_align_state import LAST_USED_NAME
from tag_align_state import PresetFormatError
from tag_align_state import PresetInfo
from tag_align_state import find_preset
from tag_align_state import read_payload
from tag_align_state import remove_preset
from tag_align_state import safe_text
from tag_align_state import sort_presets
from tag_align_state import upsert_preset
from tag_align_state import write_payload


SOURCE_LOCAL = "local"
SOURCE_MODEL = "model"
SOURCE_SHARED = "shared"

SOURCE_LABELS = {
    SOURCE_LOCAL: "This computer",
    SOURCE_MODEL: "This model",
    SOURCE_SHARED: "Shared folder",
}

SOURCE_HINTS = {
    SOURCE_LOCAL: "Available in every model you open. Only you see it.",
    SOURCE_MODEL: "Travels with the model. On an ACC cloud model your team "
                  "sees it after Sync to Central.",
    SOURCE_SHARED: "A file on a network drive or an ACC Desktop Connector "
                   "folder. Your team sees it straight away.",
}

LOCAL_FILE_ID = "EasyBIM_TagAlign_presets"
SHARED_FILE_NAME = "EasyBIM_TagAlign_presets.json"


def local_preset_path():
    """pyRevit's roaming per-user data file. Imported lazily so this module
    can be loaded (and tested) without pyRevit on the path."""
    from pyrevit import script

    return script.get_universal_data_file(LOCAL_FILE_ID, "json")


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------


class PresetSource(object):
    """One place presets live."""

    def __init__(self, key, label=None, hint=None):
        self.key = key
        self.label = label or SOURCE_LABELS.get(key, key)
        self.hint = hint or SOURCE_HINTS.get(key, "")
        self.available = True
        self.unavailable_reason = ""
        self.last_error = ""

    def read(self):
        """Return a normalised payload; never raise."""
        raise NotImplementedError

    def write(self, payload):
        """Return ``(ok, error_text)``; never raise."""
        raise NotImplementedError

    def describe(self):
        return self.label


def _empty_payload():
    return write_payload([], "")


class FileSource(PresetSource):
    def __init__(self, key, path, label=None, hint=None):
        PresetSource.__init__(self, key, label, hint)
        self.path = safe_text(path)
        if not self.path:
            self.available = False
            self.unavailable_reason = "No file path is set."

    def describe(self):
        return "{0} ({1})".format(self.label, self.path) if self.path else self.label

    def read(self):
        self.last_error = ""
        if not self.path or not os.path.isfile(self.path):
            return _empty_payload()
        try:
            with io.open(self.path, "r", encoding="utf-8") as handle:
                raw = json.loads(handle.read() or "{}")
        except Exception as ex:
            # A truncated or hand-edited file must not take the tool down; the
            # user gets told, and the other sources still work.
            self.last_error = "Could not read {0}: {1}".format(self.path, ex)
            return _empty_payload()

        try:
            return read_payload(raw)
        except PresetFormatError as ex:
            self.last_error = safe_text(ex)
            return _empty_payload()

    def write(self, payload):
        self.last_error = ""
        if not self.path:
            return False, "No file path is set."

        folder = os.path.dirname(self.path)
        try:
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
        except Exception as ex:
            return False, "Could not create {0}: {1}".format(folder, ex)

        text = json.dumps(payload, indent=2, sort_keys=True)
        temporary = self.path + ".tmp"
        try:
            with io.open(temporary, "w", encoding="utf-8") as handle:
                handle.write(text if isinstance(text, type(u"")) else text.decode("utf-8"))
            # Write beside the target and swap, so a crash mid-write cannot
            # leave the real file half-written.
            if os.path.isfile(self.path):
                os.remove(self.path)
            os.rename(temporary, self.path)
        except Exception as ex:
            try:
                if os.path.isfile(temporary):
                    os.remove(temporary)
            except Exception:
                pass
            return False, "Could not write {0}: {1}".format(self.path, ex)
        return True, ""


class CallableSource(PresetSource):
    """In-model storage: the Revit layer supplies the two callables."""

    def __init__(self, key, reader, writer, label=None, hint=None,
                 unavailable_reason=""):
        PresetSource.__init__(self, key, label, hint)
        self.reader = reader
        self.writer = writer
        if unavailable_reason or reader is None:
            self.available = False
            self.unavailable_reason = unavailable_reason or "No document is open."

    def read(self):
        self.last_error = ""
        if not self.available or self.reader is None:
            return _empty_payload()
        try:
            raw = self.reader()
        except Exception as ex:
            self.last_error = "Could not read the model's presets: {0}".format(ex)
            return _empty_payload()

        if isinstance(raw, type(u"")) or isinstance(raw, bytes):
            try:
                raw = json.loads(raw or "{}")
            except Exception as ex:
                self.last_error = "The model's presets are unreadable: {0}".format(ex)
                return _empty_payload()

        try:
            return read_payload(raw)
        except PresetFormatError as ex:
            self.last_error = safe_text(ex)
            return _empty_payload()

    def write(self, payload):
        self.last_error = ""
        if not self.available or self.writer is None:
            return False, self.unavailable_reason or "Saving to the model is unavailable."
        try:
            return self.writer(json.dumps(payload, indent=2, sort_keys=True))
        except Exception as ex:
            return False, "Could not save into the model: {0}".format(ex)


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------


class PresetLibrary(object):
    """The three sources presented as one list."""

    def __init__(self, sources=None):
        self.sources = list(sources or [])
        self._cache = {}

    def source(self, key):
        for source in self.sources:
            if source.key == key:
                return source
        return None

    def writable_sources(self):
        return [source for source in self.sources if source.available]

    def payload(self, key, refresh=False):
        if refresh or key not in self._cache:
            source = self.source(key)
            self._cache[key] = source.read() if source else _empty_payload()
        return self._cache[key]

    def invalidate(self, key=None):
        if key is None:
            self._cache = {}
        else:
            self._cache.pop(key, None)

    def errors(self):
        return [source.last_error for source in self.sources if source.last_error]

    def infos(self):
        """Every preset from every source, Last used first."""
        rows = []
        for source in self.sources:
            if not source.available:
                continue
            for entry in self.payload(source.key).get("presets") or []:
                rows.append(PresetInfo(safe_text(entry.get("name")), source, entry))
        return sort_presets(rows)

    def get(self, source_key, name):
        return find_preset(self.payload(source_key).get("presets"), name)

    def exists(self, source_key, name):
        return self.get(source_key, name) is not None

    def save(self, source_key, preset):
        source = self.source(source_key)
        if source is None:
            return False, "Unknown destination."
        if not source.available:
            return False, source.unavailable_reason or "That destination is unavailable."

        payload = self.payload(source_key, refresh=True)
        presets = upsert_preset(payload.get("presets"), preset)
        ok, error = source.write(write_payload(presets, payload.get("shared_path")))
        self.invalidate(source_key)
        return ok, error

    def delete(self, source_key, name):
        if safe_text(name) == LAST_USED_NAME:
            return False, "Last used is written automatically and cannot be deleted."

        source = self.source(source_key)
        if source is None:
            return False, "Unknown destination."
        if not source.available:
            return False, source.unavailable_reason or "That destination is unavailable."

        payload = self.payload(source_key, refresh=True)
        presets = remove_preset(payload.get("presets"), name)
        ok, error = source.write(write_payload(presets, payload.get("shared_path")))
        self.invalidate(source_key)
        return ok, error

    def rename(self, source_key, old_name, new_name):
        if safe_text(old_name) == LAST_USED_NAME:
            return False, "Last used is written automatically and cannot be renamed."
        new_name = safe_text(new_name).strip()
        if not new_name:
            return False, "Enter a name."
        if new_name == LAST_USED_NAME:
            return False, "That name is reserved."

        preset = self.get(source_key, old_name)
        if preset is None:
            return False, "That preset is gone."

        payload = self.payload(source_key, refresh=True)
        presets = remove_preset(payload.get("presets"), old_name)
        renamed = dict(preset)
        renamed["name"] = new_name
        presets = upsert_preset(presets, renamed)

        source = self.source(source_key)
        ok, error = source.write(write_payload(presets, payload.get("shared_path")))
        self.invalidate(source_key)
        return ok, error

    # -- shared path -------------------------------------------------------
    #
    # Kept in the local payload rather than pyRevit's config, so the whole
    # feature has exactly one storage mechanism to go wrong.

    def shared_path(self):
        return safe_text(self.payload(SOURCE_LOCAL).get("shared_path"))

    def set_shared_path(self, path):
        source = self.source(SOURCE_LOCAL)
        if source is None:
            return False, "The local preset file is unavailable."
        payload = self.payload(SOURCE_LOCAL, refresh=True)
        ok, error = source.write(
            write_payload(payload.get("presets"), safe_text(path))
        )
        self.invalidate(SOURCE_LOCAL)
        return ok, error


def resolve_shared_file(path):
    """Accept either the folder or the file itself, and return the file.

    Decided on the name rather than on ``isdir``, so a folder the user picked
    but has not created yet still resolves to a file inside it instead of
    becoming an extensionless file with the folder's name.
    """
    path = safe_text(path).strip().rstrip("\\/")
    if not path:
        return ""
    if path.lower().endswith(".json"):
        return path
    return os.path.join(path, SHARED_FILE_NAME)


def build_library(local_path=None, model_reader=None, model_writer=None,
                  model_unavailable_reason="", shared_path=None):
    """Assemble the three sources.

    The shared path is discovered from the local payload unless one is passed
    in, which is what lets the Save window offer a folder the user picked in a
    previous session.
    """
    if local_path is None:
        try:
            local_path = local_preset_path()
        except Exception:
            local_path = ""

    local = FileSource(SOURCE_LOCAL, local_path)

    model = CallableSource(
        SOURCE_MODEL,
        model_reader,
        model_writer,
        unavailable_reason=model_unavailable_reason,
    )

    if shared_path is None:
        shared_path = safe_text(local.read().get("shared_path"))
    shared_file = resolve_shared_file(shared_path)

    shared = FileSource(SOURCE_SHARED, shared_file)
    if not shared_file:
        shared.available = False
        shared.unavailable_reason = "Pick a shared folder first."

    return PresetLibrary([local, model, shared])
