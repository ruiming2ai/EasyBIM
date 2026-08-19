# -*- coding: utf-8 -*-
"""My Ribbon - pure logic: git links, names, the staged registry, import/export.

Nothing here touches Revit, pyRevit or the file system, so the whole module
runs and is tested on desktop Python.  The registry it edits is the plain dict
``easybim.my_ribbon`` reads and writes; this module never loads or saves it.
"""

from __future__ import print_function

import copy
import json
import re


DEFAULT_PANEL_NAME = "My Tools"
DEFAULT_TAB_NAME = "My Ribbon"

#: pyRevit bundle kinds that put nothing (or nothing shareable) on a panel.
NOT_PLACEABLE = {
    "nobutton": "runs without a button",
    "panelbutton": "is the panel's small corner button",
    "combobox": "is a drop-down list bound to its own panel",
    "combo": "is a drop-down list bound to its own panel",
}
#: Live ribbon items of a type My Ribbon does not share (galleries, lists,
#: text boxes, sliders...) come through as kind ``ribbon-<typename>``.
LIVE_REFUSED_PREFIX = "ribbon-"

#: Fields a source may carry beyond the common ones, per kind.
SOURCE_EXTRA_FIELDS = {
    "catalogue": ("name",),
    "dynamo": ("path", "title", "bundle", "icon"),
}

_WINDOWS_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# -- text helpers -----------------------------------------------------------


def safe_text(value):
    if value is None:
        return u""
    try:
        return value if isinstance(value, type(u"")) else str(value)
    except Exception:
        return u""


def normalize_label(value):
    text = safe_text(value).replace("\\n", " ").replace("\n", " ")
    return " ".join(text.split()).lower()


def sanitize_folder_name(text, fallback="Extension"):
    """A name Windows accepts as a folder: no reserved chars, no trailing dots
    or spaces, never empty."""
    cleaned = _WINDOWS_BAD_CHARS.sub("_", safe_text(text)).strip().rstrip(". ")
    cleaned = " ".join(cleaned.split())
    return cleaned or fallback


# -- git links ----------------------------------------------------------------


class GitRef(object):
    """One parsed repository link."""

    def __init__(self, host, path_parts, branch=None, subpath=None, scheme="https"):
        self.host = host.lower()
        self.path_parts = [p for p in path_parts if p]
        self.branch = branch or None
        self.subpath = subpath or None
        self.scheme = scheme

    @property
    def repo(self):
        return self.path_parts[-1] if self.path_parts else ""

    @property
    def owner(self):
        parts = [p for p in self.path_parts if p != "_git"]  # Azure DevOps marker
        return parts[-2] if len(parts) >= 2 else ""

    @property
    def clone_url(self):
        return "{0}://{1}/{2}.git".format(self.scheme, self.host, "/".join(self.path_parts))

    @property
    def web_url(self):
        return "{0}://{1}/{2}".format(self.scheme, self.host, "/".join(self.path_parts))

    @property
    def key(self):
        base = "{0}/{1}".format(self.host, "/".join(self.path_parts)).lower()
        return base + ("@" + self.branch if self.branch else "")

    @property
    def label(self):
        if self.owner:
            return "{0}/{1}".format(self.owner, self.repo)
        return self.repo

    def as_dict(self):
        return {
            "host": self.host,
            "owner": self.owner,
            "repo": self.repo,
            "branch": self.branch,
            "subpath": self.subpath,
            "clone_url": self.clone_url,
            "web_url": self.web_url,
            "key": self.key,
            "label": self.label,
        }


_SCP_LIKE = re.compile(r"^(?:[\w.-]+@)?([\w.-]+):(?!//)([\w./~-]+?)(?:\.git)?/?$")
_URL = re.compile(r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)://(?P<rest>.+)$")


def parse_git_url(text):
    """Return ``(GitRef, error)``.  One of the two is always None.

    Accepts the forms people actually paste: ``https://github.com/o/r``,
    with or without ``.git`` or a trailing slash, ``…/tree/<branch>/<sub>``
    and ``…/blob/<branch>/<file>``, ``git@github.com:o/r.git`` and
    ``ssh://git@host/o/r`` (both turned into their https clone URL, since
    pyRevit's git library speaks https), and plain https links to GitLab,
    Bitbucket, Azure DevOps or a self-hosted server.
    """
    raw = safe_text(text).strip()
    if not raw:
        return None, "Paste a link to a git repository first."
    if any(ch.isspace() for ch in raw):
        return None, "That does not look like one link - it contains spaces."

    scp = _SCP_LIKE.match(raw)
    if scp and "://" not in raw:
        host, path = scp.group(1), scp.group(2)
        if len(host) == 1:
            return None, "That is a local path. Paste the repository's web link instead."
        parts = [p for p in path.split("/") if p]
        if len(parts) < 1:
            return None, "The link has no repository name."
        return GitRef(host, parts), None

    match = _URL.match(raw)
    if not match:
        if re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", raw, re.IGNORECASE):
            return parse_git_url("https://" + raw)
        if re.match(r"^[\w.-]+/[\w.-]+$", raw):
            # GitHub shorthand: owner/repo
            return parse_git_url("https://github.com/" + raw)
        return None, "That is not a web link. It should start with https:// (or git@)."
    scheme = match.group("scheme").lower()
    rest = match.group("rest")
    if scheme in ("ssh", "git+ssh", "git"):
        scheme = "https"
    elif scheme not in ("http", "https"):
        return None, "Only https:// (or git@) links can be downloaded."
    if scheme == "http":
        scheme = "https"

    rest = rest.split("#", 1)[0].split("?", 1)[0]
    if "@" in rest.split("/", 1)[0]:
        rest = rest.split("@", 1)[1]
    host, _, path = rest.partition("/")
    host = host.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host and host != "localhost":
        return None, "The link has no server name."
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None, "The link points at the server, not at a repository."

    branch = subpath = None
    # "…/tree/<branch>/<sub>" (GitHub), "…/-/tree/<branch>" (GitLab),
    # "…/src/<branch>" (Bitbucket).  A marker only counts after owner/repo,
    # so a repository that happens to be called "src" or "tree" survives.
    for marker in (["-", "tree"], ["-", "blob"], ["tree"], ["blob"], ["src"]):
        cut = None
        for index in range(2, len(parts) - len(marker) + 1):
            if [p.lower() for p in parts[index:index + len(marker)]] == marker:
                cut = index
                break
        if cut is not None:
            tail = parts[cut + len(marker):]
            parts = parts[:cut]
            if tail:
                branch = tail[0]
                subpath = "/".join(tail[1:]) or None
            break
    if parts and parts[-1].lower().endswith(".git"):
        parts[-1] = parts[-1][:-4]
    parts = [p for p in parts if p]
    if not parts:
        return None, "The link points at the server, not at a repository."
    return GitRef(host, parts, branch=branch, subpath=subpath, scheme=scheme), None


# -- extension folder names ---------------------------------------------------


def strip_extension_suffix(name):
    text = safe_text(name).strip()
    for suffix in (".extension", ".lib"):
        if text.lower().endswith(suffix):
            return text[:-len(suffix)]
    return text


def extension_dir_name(ref, existing_names, manifest_name=None):
    """Pick ``<Name>.extension`` for a clone, avoiding names already in use.

    pyRevit's own installer names the folder after the ``extension.json``
    name; without a manifest the repository name is used, minus a
    ``.extension`` suffix people sometimes put in the repo name.  A clash with
    a *different* extension gets the owner appended, then a counter.
    """
    base = sanitize_folder_name(strip_extension_suffix(manifest_name or ""))
    if not base or base == "Extension":
        base = sanitize_folder_name(strip_extension_suffix(ref.repo if ref else ""))
    taken = set(normalize_label(n) for n in (existing_names or []))
    candidates = [base]
    if ref is not None and ref.owner:
        candidates.append("{0} ({1})".format(base, ref.owner))
    for candidate in candidates:
        if normalize_label(candidate + ".extension") not in taken:
            return candidate + ".extension"
    stem = candidates[-1]
    counter = 2
    while True:
        candidate = "{0} {1}.extension".format(stem, counter)
        if normalize_label(candidate) not in taken:
            return candidate
        counter += 1


# -- registry keys and ids ----------------------------------------------------


def source_key(source):
    """What makes two sources 'the same' regardless of how they were added."""
    kind = safe_text(source.get("kind")).lower()
    if kind == "git" and source.get("url"):
        ref, _ = parse_git_url(source.get("url"))
        base = ref.key if ref else safe_text(source.get("url")).lower()
        branch = safe_text(source.get("branch"))
        if ref is None and branch:
            base += "@" + branch
        elif ref is not None and branch and not ref.branch:
            base += "@" + branch
        # One repository can hold several extensions (a monorepo): each is
        # its own source, told apart by the extension it names.
        ext_name = normalize_label(source.get("ext_name"))
        if ext_name:
            base += "#" + ext_name
        return "git:" + base
    if kind == "catalogue":
        return "cat:" + normalize_label(source.get("name") or source.get("ext_name"))
    if kind == "ribbon":
        return "rib:" + normalize_label(source.get("ext_name"))
    if kind == "dynamo":
        return "dyn:" + normalize_path(source.get("path"))
    return "ext:" + normalize_label(source.get("ext_name"))


def normalize_path(path):
    """Case- and separator-insensitive key for a Windows file path."""
    text = safe_text(path).strip().replace("/", "\\")
    while "\\\\" in text[2:]:
        text = text[:2] + text[2:].replace("\\\\", "\\")
    return text.rstrip("\\").lower()


def path_key(path):
    return "/".join(normalize_label(level.get("name") or level.get("title"))
                    for level in (path or []) if isinstance(level, dict))


def placement_key(source_key_text, path):
    return "{0}|{1}".format(source_key_text, path_key(path))


def new_id(prefix, existing_ids):
    taken = set(safe_text(i) for i in existing_ids)
    counter = 1
    while True:
        candidate = "{0}{1}".format(prefix, counter)
        if candidate not in taken:
            return candidate
        counter += 1


def _ids(registry, section):
    return [item.get("id") for item in registry.get(section, [])]


# -- staged edits (all return the thing they touched; all mutate in place) --


def find_source(registry, source):
    """Find an existing source equal to ``source`` (same key)."""
    wanted = source_key(source)
    for existing in registry.get("sources", []):
        if source_key(existing) == wanted:
            return existing
    return None


def find_source_by_id(registry, source_id):
    for existing in registry.get("sources", []):
        if existing.get("id") == source_id:
            return existing
    return None


def add_source(registry, source):
    """Add (or return the already-present) source; ``source`` needs no id."""
    existing = find_source(registry, source)
    if existing is not None:
        # refresh the facts that can legitimately change
        for field in ("tab_names", "label", "ext_name", "extra_root"):
            if source.get(field):
                existing[field] = source[field]
        return existing
    entry = {
        "id": new_id("s", _ids(registry, "sources")),
        "kind": safe_text(source.get("kind")) or "git",
        "url": source.get("url") or None,
        "branch": source.get("branch") or None,
        "ext_name": safe_text(source.get("ext_name")),
        "label": safe_text(source.get("label")) or safe_text(source.get("ext_name")),
        "tab_names": list(source.get("tab_names") or []),
        "installed_by_my_ribbon": bool(source.get("installed_by_my_ribbon", False)),
        "hide_tab": bool(source.get("hide_tab", False)),
        "extra_root": source.get("extra_root") or None,
    }
    for field in SOURCE_EXTRA_FIELDS.get(entry["kind"], ()):
        entry[field] = source.get(field)
    if entry["kind"] == "catalogue":
        entry["name"] = safe_text(source.get("name")) or entry["ext_name"]
    if entry["kind"] == "dynamo":
        entry["title"] = safe_text(source.get("title")) or safe_text(source.get("label")) \
            or entry["ext_name"]
        if not safe_text(source.get("label")):
            entry["label"] = entry["title"]
    registry.setdefault("sources", []).append(entry)
    if entry.get("hide_tab"):
        set_tabs_hidden(registry, entry.get("tab_names") or [], True)
    return entry


def remove_source(registry, source_id):
    """Drop a source and every placement that came from it.  Tabs that were
    hidden *because of* this source become visible again unless another
    source still hides them."""
    source = find_source_by_id(registry, source_id)
    removed = [p for p in registry.get("placements", []) if p.get("source") == source_id]
    registry["placements"] = [p for p in registry.get("placements", [])
                              if p.get("source") != source_id]
    registry["sources"] = [s for s in registry.get("sources", []) if s.get("id") != source_id]
    if source is not None and source.get("hide_tab"):
        still_hidden = set()
        for other in registry.get("sources", []):
            if other.get("hide_tab"):
                for name in other.get("tab_names") or []:
                    still_hidden.add(normalize_label(name))
        to_show = [n for n in (source.get("tab_names") or [])
                   if normalize_label(n) not in still_hidden]
        set_tabs_hidden(registry, to_show, False)
    renumber(registry)
    return removed


def set_hide_tab(registry, source_id, value):
    """The per-source shortcut: hide/show every tab the source owns."""
    source = find_source_by_id(registry, source_id)
    if source is not None:
        source["hide_tab"] = bool(value)
        set_tabs_hidden(registry, source.get("tab_names") or [], bool(value))
    return source


# -- tab visibility -----------------------------------------------------------


def hidden_tabs(registry):
    return list(registry.get("hidden_tabs") or [])


def is_tab_hidden(registry, tab_name):
    key = normalize_label(tab_name)
    return any(normalize_label(n) == key for n in registry.get("hidden_tabs") or [])


def set_tabs_hidden(registry, tab_names, hidden):
    """Add (or remove) tab names on the registry's hidden list, keeping
    order and ignoring case; returns the list."""
    current = list(registry.get("hidden_tabs") or [])
    for name in tab_names or []:
        name = safe_text(name).strip()
        if not name:
            continue
        key = normalize_label(name)
        present = [n for n in current if normalize_label(n) == key]
        if hidden and not present:
            current.append(name)
        elif not hidden and present:
            current = [n for n in current if normalize_label(n) != key]
    registry["hidden_tabs"] = current
    return current


def replace_hidden_tabs(registry, tab_names):
    """The Show/Hide window's result: the complete hidden list.  Per-source
    ``hide_tab`` flags are re-derived so the two views never disagree."""
    registry["hidden_tabs"] = []
    set_tabs_hidden(registry, tab_names or [], True)
    sync_source_hide_flags(registry)
    return registry["hidden_tabs"]


def sync_source_hide_flags(registry):
    """``hide_tab`` is true when every tab the source owns is hidden."""
    for source in registry.get("sources", []):
        names = source.get("tab_names") or []
        source["hide_tab"] = bool(names) and all(is_tab_hidden(registry, n) for n in names)
    return registry


def find_destination(registry, tab, panel):
    for existing in registry.get("destinations", []):
        if normalize_label(existing.get("tab")) == normalize_label(tab) \
                and normalize_label(existing.get("panel")) == normalize_label(panel):
            return existing
    return None


def find_destination_by_id(registry, dest_id):
    for existing in registry.get("destinations", []):
        if existing.get("id") == dest_id:
            return existing
    return None


def add_destination(registry, tab, panel, own_tab=False):
    tab = safe_text(tab).strip() or DEFAULT_TAB_NAME
    panel = safe_text(panel).strip() or DEFAULT_PANEL_NAME
    existing = find_destination(registry, tab, panel)
    if existing is not None:
        return existing
    entry = {
        "id": new_id("d", _ids(registry, "destinations")),
        "tab": tab,
        "panel": panel,
        "own_tab": bool(own_tab),
    }
    registry.setdefault("destinations", []).append(entry)
    return entry


def rename_destination(registry, dest_id, tab=None, panel=None):
    dest = find_destination_by_id(registry, dest_id)
    if dest is None:
        return None
    if tab is not None and safe_text(tab).strip():
        dest["tab"] = safe_text(tab).strip()
    if panel is not None and safe_text(panel).strip():
        dest["panel"] = safe_text(panel).strip()
    return dest


def remove_destination(registry, dest_id):
    removed = [p for p in registry.get("placements", []) if p.get("dest") == dest_id]
    registry["placements"] = [p for p in registry.get("placements", [])
                              if p.get("dest") != dest_id]
    registry["destinations"] = [d for d in registry.get("destinations", [])
                                if d.get("id") != dest_id]
    return removed


def find_placement(registry, source_id, path):
    """The placement of this button, wherever it sits (one per button)."""
    wanted = path_key(path)
    for placement in registry.get("placements", []):
        if placement.get("source") == source_id and path_key(placement.get("path")) == wanted:
            return placement
    return None


def find_placement_by_id(registry, placement_id):
    for placement in registry.get("placements", []):
        if placement.get("id") == placement_id:
            return placement
    return None


def add_placement(registry, source_id, dest_id, button):
    """Place ``button`` (an adapter dict: kind/title/control_id/path) at the
    end of ``dest``.  Returns the placement, existing or new."""
    existing = find_placement(registry, source_id, button.get("path"))
    if existing is not None:
        return existing
    orders = [p.get("order", 0) for p in registry.get("placements", [])
              if p.get("dest") == dest_id]
    entry = {
        "id": new_id("p", _ids(registry, "placements")),
        "source": source_id,
        "dest": dest_id,
        "order": (max(orders) + 1) if orders else 0,
        "kind": safe_text(button.get("kind")) or "button",
        "title": safe_text(button.get("title")),
        "control_id": safe_text(button.get("control_id")),
        "path": [dict(level) for level in (button.get("path") or [])],
    }
    registry.setdefault("placements", []).append(entry)
    return entry


def remove_placement(registry, placement_id):
    before = len(registry.get("placements", []))
    registry["placements"] = [p for p in registry.get("placements", [])
                              if p.get("id") != placement_id]
    renumber(registry)
    return len(registry["placements"]) != before


def placements_in(registry, dest_id):
    rows = [p for p in registry.get("placements", []) if p.get("dest") == dest_id]
    return sorted(rows, key=lambda p: (p.get("order", 0), p.get("id", "")))


def move_placement(registry, placement_id, delta):
    """Move up (delta < 0) or down (delta > 0) inside its panel."""
    placement = find_placement_by_id(registry, placement_id)
    if placement is None:
        return False
    rows = placements_in(registry, placement.get("dest"))
    index = [r.get("id") for r in rows].index(placement_id)
    target = index + delta
    if target < 0 or target >= len(rows):
        return False
    rows.insert(target, rows.pop(index))
    for order, row in enumerate(rows):
        row["order"] = order
    return True


def move_placement_to(registry, placement_id, dest_id):
    placement = find_placement_by_id(registry, placement_id)
    if placement is None or find_destination_by_id(registry, dest_id) is None:
        return False
    if placement.get("dest") == dest_id:
        return True
    orders = [p.get("order", 0) for p in registry.get("placements", [])
              if p.get("dest") == dest_id]
    placement["dest"] = dest_id
    placement["order"] = (max(orders) + 1) if orders else 0
    renumber(registry)
    return True


def renumber(registry):
    """Dense 0..n orders per destination, keeping the current sequence."""
    for dest in registry.get("destinations", []):
        for order, row in enumerate(placements_in(registry, dest.get("id"))):
            row["order"] = order
    return registry


def summarize(registry):
    return {
        "sources": len(registry.get("sources", [])),
        "destinations": len(registry.get("destinations", [])),
        "placements": len(registry.get("placements", [])),
    }


def count_changes(saved, working):
    """How many sources/destinations/placements differ between two registries."""
    changes = 0
    for section in ("sources", "destinations", "placements"):
        before = dict((item.get("id"), item) for item in saved.get(section, []))
        after = dict((item.get("id"), item) for item in working.get(section, []))
        for key in set(before) | set(after):
            if before.get(key) != after.get(key):
                changes += 1
    before_tabs = set(normalize_label(n) for n in saved.get("hidden_tabs") or [])
    after_tabs = set(normalize_label(n) for n in working.get("hidden_tabs") or [])
    changes += len(before_tabs ^ after_tabs)
    return changes


def status_line(registry, changes):
    parts = summarize(registry)
    text = "{0} source{1} · {2} button{3}".format(
        parts["sources"], "" if parts["sources"] == 1 else "s",
        parts["placements"], "" if parts["placements"] == 1 else "s")
    if changes:
        text += u" · {0} change{1} not applied".format(changes, "" if changes == 1 else "s")
    return text


# -- picker rows ------------------------------------------------------------


def button_tags(button, host_version=None):
    """Short labels shown next to a picker row.  ``button`` is an adapter dict
    with ``kind``, ``min_revit``, ``max_revit``, ``in_layout``."""
    tags = []
    kind = safe_text(button.get("kind")).lower()
    if kind in NOT_PLACEABLE:
        tags.append("cannot be placed: " + NOT_PLACEABLE[kind])
    elif kind.startswith(LIVE_REFUSED_PREFIX):
        tags.append("cannot be placed: a {0} only works on its own panel".format(
            kind[len(LIVE_REFUSED_PREFIX):].replace("ribbon", "") or "control"))
    elif kind in ("pulldown", "splitbutton", "splitpushbutton"):
        tags.append("whole drop-down")
    version_tag = revit_version_tag(button.get("min_revit"), button.get("max_revit"), host_version)
    if version_tag:
        tags.append(version_tag)
    if button.get("in_layout") is False:
        tags.append("not shown in its own ribbon")
    return tags


def revit_version_tag(min_version, max_version, host_version=None):
    low = _int_or_none(min_version)
    high = _int_or_none(max_version)
    host = _int_or_none(host_version)
    if low and high:
        text = "Revit {0}-{1}".format(low, high)
    elif low:
        text = "Revit {0}+".format(low)
    elif high:
        text = "Revit up to {0}".format(high)
    else:
        return ""
    if host and ((low and host < low) or (high and host > high)):
        text += " (not this Revit)"
    return text


def is_placeable(button):
    kind = safe_text(button.get("kind")).lower()
    return kind not in NOT_PLACEABLE and not kind.startswith(LIVE_REFUSED_PREFIX)


def _int_or_none(value):
    try:
        number = int(safe_text(value).strip()[:4]) if safe_text(value).strip() else None
    except (TypeError, ValueError):
        return None
    return number or None


# -- export / import ----------------------------------------------------------


def export_document(registry):
    """The registry as written to an export file: same shape, own copy."""
    document = {
        "format": registry.get("format", 1),
        "exported_by": "EasyBIM My Ribbon",
        "sources": copy.deepcopy(registry.get("sources", [])),
        "destinations": copy.deepcopy(registry.get("destinations", [])),
        "placements": copy.deepcopy(registry.get("placements", [])),
        "hidden_tabs": list(registry.get("hidden_tabs") or []),
    }
    return document


def plan_import(current, incoming, mode="merge", installed_ext_names=None):
    """Work out what importing ``incoming`` into ``current`` would do.

    Both registries must already be normalised.  Returns a plan dict with
    the resulting registry under ``result`` and human-readable lists:
    ``sources_added``, ``sources_reused``, ``sources_to_install`` (git or
    catalogue sources not installed here), ``sources_not_here`` (installed-
    kind sources this computer lacks), ``destinations_added``,
    ``placements_added``, ``placements_skipped`` (already placed), and
    ``mode``.  Nothing is applied; the caller decides.
    """
    installed = set(normalize_label(n) for n in (installed_ext_names or []))
    plan = {
        "mode": mode,
        "sources_added": [],
        "sources_reused": [],
        "sources_to_install": [],
        "sources_not_here": [],
        "destinations_added": [],
        "placements_added": [],
        "placements_skipped": [],
        "tabs_hidden": [],
    }
    if mode == "replace":
        result = {"format": current.get("format", 1), "sources": [],
                  "destinations": [], "placements": [], "hidden_tabs": []}
    else:
        result = copy.deepcopy(current)
        result.setdefault("hidden_tabs", [])
    before_hidden = set(normalize_label(n) for n in result.get("hidden_tabs") or [])
    set_tabs_hidden(result, incoming.get("hidden_tabs") or [], True)

    source_map = {}
    for source in incoming.get("sources", []):
        existing = find_source(result, source)
        if existing is not None:
            source_map[source.get("id")] = existing.get("id")
            plan["sources_reused"].append(existing.get("label") or existing.get("ext_name"))
            continue
        # Never trust the file about what *this* computer installed: only a
        # download made here may set installed_by_my_ribbon (it decides what
        # Remove is allowed to delete), and a colleague's registered root path
        # means nothing on this machine.
        entry = add_source(result, dict(source, id=None, installed_by_my_ribbon=False,
                                        extra_root=None))
        source_map[source.get("id")] = entry["id"]
        plan["sources_added"].append(entry.get("label") or entry.get("ext_name"))
    for source in result.get("sources", []):
        name = normalize_label(source.get("ext_name"))
        kind = source.get("kind")
        if kind in ("installed", "ribbon"):
            if name and name not in installed and kind == "installed":
                plan["sources_not_here"].append(source.get("label") or source.get("ext_name"))
        elif kind == "dynamo":
            # the graph path is checked by the host; nothing to install
            continue
        elif name not in installed:
            plan["sources_to_install"].append(source.get("label") or source.get("ext_name"))

    dest_map = {}
    for dest in incoming.get("destinations", []):
        existing = find_destination(result, dest.get("tab"), dest.get("panel"))
        if existing is None:
            entry = add_destination(result, dest.get("tab"), dest.get("panel"), dest.get("own_tab"))
            plan["destinations_added"].append("{0} > {1}".format(entry["tab"], entry["panel"]))
        else:
            entry = existing
        dest_map[dest.get("id")] = entry["id"]

    for placement in sorted(incoming.get("placements", []),
                            key=lambda p: (p.get("dest", ""), p.get("order", 0))):
        source_id = source_map.get(placement.get("source"))
        dest_id = dest_map.get(placement.get("dest"))
        if source_id is None or dest_id is None:
            plan["placements_skipped"].append(
                "{0} (its source or panel is missing from the file)".format(placement.get("title")))
            continue
        if find_placement(result, source_id, placement.get("path")) is not None:
            plan["placements_skipped"].append("{0} (already placed)".format(placement.get("title")))
            continue
        entry = add_placement(result, source_id, dest_id, placement)
        plan["placements_added"].append(entry.get("title"))
    renumber(result)
    sync_source_hide_flags(result)
    # reported last: sources added above may hide their own tabs too
    plan["tabs_hidden"] = [n for n in result["hidden_tabs"] if normalize_label(n) not in before_hidden]
    plan["result"] = result
    return plan


def format_import_preview(plan):
    """Plain sentences for the preview window."""
    lines = []
    mode = "Replace everything with the file" if plan.get("mode") == "replace" \
        else "Merge the file into what you have"
    lines.append(mode + ".")
    added = plan.get("sources_added", [])
    reused = plan.get("sources_reused", [])
    lines.append("Sources: {0} new{1}, {2} already linked{3}.".format(
        len(added), _listing(added), len(reused), _listing(reused)))
    to_install = plan.get("sources_to_install", [])
    if to_install:
        lines.append("To download and install here: {0}{1}.".format(
            len(to_install), _listing(to_install)))
    not_here = plan.get("sources_not_here", [])
    if not_here:
        lines.append("Not installed on this computer (their buttons will show as missing "
                     "until you install them): {0}.".format(", ".join(not_here)))
    dests = plan.get("destinations_added", [])
    lines.append("Panels: {0} new{1}.".format(len(dests), _listing(dests)))
    placed = plan.get("placements_added", [])
    skipped = plan.get("placements_skipped", [])
    lines.append("Buttons: {0} to add{1}.".format(len(placed), _listing(placed)))
    if skipped:
        lines.append("Skipped: {0}.".format("; ".join(skipped)))
    tabs = plan.get("tabs_hidden", [])
    if tabs:
        lines.append("Tabs to hide: {0}.".format(", ".join(tabs)))
    return lines


def _listing(items, limit=6):
    if not items:
        return ""
    shown = [safe_text(i) for i in items[:limit]]
    more = len(items) - len(shown)
    text = ", ".join(shown)
    if more > 0:
        text += ", and {0} more".format(more)
    return " ({0})".format(text)


# -- Dynamo graphs ----------------------------------------------------------------


def dynamo_facts_from_text(text, file_name=""):
    """What a picked file is, read from its content: ``format`` (``"2.x"``
    JSON, ``"1.x"`` XML or ``"unknown"``), ``is_custom_node``, ``name``,
    ``python_engines`` (e.g. ``["CPython3"]``), ``packages`` (names from the
    2.x dependency list) and ``problem`` (a sentence when the file is not a
    runnable graph).  Never raises."""
    facts = {"format": "unknown", "is_custom_node": False, "name": "",
             "python_engines": [], "packages": [], "problem": ""}
    lowered = safe_text(file_name).lower()
    if lowered.endswith(".dyf"):
        facts["is_custom_node"] = True
        facts["problem"] = "This is a Dynamo custom node (.dyf), not a graph that can run on its own."
    elif lowered.endswith(".py"):
        facts["problem"] = "This is a Python script. A Dynamo button needs a graph (.dyn); " \
                           "put the script inside a Python node of a graph."
    raw = safe_text(text).strip()
    if raw.startswith(u"\ufeff"):
        raw = raw[1:]
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, dict):
            facts["format"] = "2.x"
            facts["name"] = safe_text(data.get("Name")).strip()
            if data.get("IsCustomNode"):
                facts["is_custom_node"] = True
                if not facts["problem"]:
                    facts["problem"] = "This graph is saved as a custom node; it cannot run on its own."
            engines = []
            for node in data.get("Nodes") or []:
                if not isinstance(node, dict):
                    continue
                concrete = safe_text(node.get("ConcreteType"))
                if "Python" in concrete:
                    engine = safe_text(node.get("Engine")).strip() or "IronPython2"
                    if engine not in engines:
                        engines.append(engine)
            facts["python_engines"] = engines
            packages = []
            for dep in data.get("NodeLibraryDependencies") or []:
                if isinstance(dep, dict):
                    name = safe_text(dep.get("Name")).strip()
                    if name and name not in packages:
                        packages.append(name)
            facts["packages"] = packages
    elif raw.startswith("<"):
        if "<Workspace" in raw[:2000]:
            facts["format"] = "1.x"
            match = re.search(r'<Workspace[^>]*\sName="([^"]*)"', raw[:4000])
            if match:
                facts["name"] = match.group(1)
            if "PythonNode" in raw:
                facts["python_engines"] = ["IronPython2"]
    if facts["format"] == "unknown" and not facts["problem"]:
        facts["problem"] = "This file does not look like a Dynamo graph (.dyn)."
    return facts


def dynamo_tags(facts):
    """Short labels for the picker/confirmation from ``dynamo_facts_from_text``."""
    tags = []
    if facts.get("format") == "1.x":
        tags.append("Dynamo 1.x graph")
    if facts.get("python_engines"):
        tags.append("contains Python nodes ({0})".format(", ".join(facts["python_engines"])))
    if facts.get("packages"):
        tags.append("uses packages: {0}".format(", ".join(facts["packages"])))
    return tags


def dynamo_bundle_name(title, existing_names):
    """``<Title>.pushbutton`` with a Windows-safe, unique folder name."""
    base = sanitize_folder_name(title, fallback="Graph")
    taken = set(normalize_label(n) for n in (existing_names or []))
    candidate = base + ".pushbutton"
    counter = 2
    while normalize_label(candidate) in taken:
        candidate = "{0} {1}.pushbutton".format(base, counter)
        counter += 1
    return candidate


def render_dynamo_bundle_yaml(title, tooltip, dynamo_path):
    """The bundle.yaml of a Dynamo button.  Strings are JSON-quoted, which
    YAML reads as double-quoted scalars, so quotes, backslashes and newlines
    in titles or paths are safe.  ``dynamo_path`` is pyRevit's own key: the
    Dynamo engine runs that file instead of the bundle's script.dyn."""
    lines = [
        "title: " + json.dumps(safe_text(title), ensure_ascii=False),
        "tooltip: " + json.dumps(safe_text(tooltip), ensure_ascii=False),
        "author: \"EasyBIM My Ribbon\"",
        "engine:",
        "  automate: true",
        "  dynamo_path: " + json.dumps(safe_text(dynamo_path), ensure_ascii=False),
    ]
    return "\n".join(lines) + "\n"


def dynamo_tooltip(path, facts=None):
    parts = ["Dynamo graph: {0}".format(safe_text(path))]
    for tag in dynamo_tags(facts or {}):
        parts.append(tag[0].upper() + tag[1:])
    parts.append("Ctrl+click opens it in Dynamo.")
    return "\n".join(parts)

