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

#: Placement rows that are layout, not buttons: a vertical separator and the
#: slide-out fold.  Kept equal to ``easybim.my_ribbon.MARKER_KINDS`` (pinned
#: by a test) - the engine draws them, this module only stages them.
MARKER_KINDS = ("separator", "slideout")

#: A stacked row holds two or three small buttons - Revit's own rule for
#: stacked items, and pyRevit's for ``.stack`` folders.
STACK_MIN = 2
STACK_MAX = 3

#: Only a plain button can join a stack: a drop-down needs its full-height
#: arrow, and a marker is not a button at all.
STACKABLE_KINDS = ("button",)

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
        # the bundle is nothing but what My Ribbon writes
        entry["installed_by_my_ribbon"] = True
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
    """The per-source shortcut: hide/show every tab the source owns.  Showing
    leaves alone a tab that another source still hides."""
    source = find_source_by_id(registry, source_id)
    if source is None:
        return None
    source["hide_tab"] = bool(value)
    names = list(source.get("tab_names") or [])
    if not value:
        names = [n for n in names if not _hidden_by_another(registry, source_id, n)]
    set_tabs_hidden(registry, names, bool(value))
    return source


def _hidden_by_another(registry, source_id, tab_name):
    key = normalize_label(tab_name)
    for other in registry.get("sources", []):
        if other.get("id") == source_id or not other.get("hide_tab"):
            continue
        if any(normalize_label(n) == key for n in other.get("tab_names") or []):
            return True
    return False


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
        # a stack id groups 2-3 rows into one row of small buttons; new
        # placements start flat and never inherit an id from another registry
        "stack": "",
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
    placement["stack"] = ""  # a row leaves its stack when it leaves the panel
    renumber(registry)
    return True


def renumber(registry):
    """Dense 0..n orders per destination, keeping the current sequence - and
    the layout invariants with them (contiguous stacks, one fold per panel)."""
    return normalize_layout(registry)


# -- panel layout: stacks and markers ---------------------------------------


def is_marker(placement):
    return safe_text(placement.get("kind")) in MARKER_KINDS


def normalize_layout(registry):
    """Restore the layout invariants and dense 0..n orders per destination.

    A stack's members sit together (the block stays where its first member
    is), only plain buttons stack, a stack of one is no stack, a stack of
    four sheds its tail, and a panel folds only once (the first slide-out
    wins; a duplicate - a hand-edited file - is dropped).
    """
    for dest in registry.get("destinations", []):
        rows = placements_in(registry, dest.get("id"))
        for row in rows:
            if row.get("stack") and safe_text(row.get("kind")) not in STACKABLE_KINDS:
                row["stack"] = ""
        ordered = []
        seen_stacks = set()
        for row in rows:
            stack_id = safe_text(row.get("stack"))
            if not stack_id:
                ordered.append(row)
            elif stack_id not in seen_stacks:
                seen_stacks.add(stack_id)
                ordered.extend(r for r in rows
                               if safe_text(r.get("stack")) == stack_id)
        kept = {}
        for row in ordered:
            stack_id = safe_text(row.get("stack"))
            if not stack_id:
                continue
            if kept.get(stack_id, 0) >= STACK_MAX:
                row["stack"] = ""
            else:
                kept[stack_id] = kept.get(stack_id, 0) + 1
        for row in ordered:
            stack_id = safe_text(row.get("stack"))
            if stack_id and kept.get(stack_id, 0) < STACK_MIN:
                row["stack"] = ""
        folded = False
        dropped = []
        for row in ordered:
            if safe_text(row.get("kind")) == "slideout":
                if folded:
                    dropped.append(row)
                folded = True
        for row in dropped:
            ordered.remove(row)
            registry["placements"].remove(row)
        for order, row in enumerate(ordered):
            row["order"] = order
    return registry


def can_group_with_next(registry, placement_id):
    """Could this row and the one below it stack together?  ``(ok, reason)``;
    the reason is the plain sentence for the refusal alert."""
    placement = find_placement_by_id(registry, placement_id)
    if placement is None:
        return False, "Select a button first."
    if is_marker(placement):
        return False, "A separator or slide-out cannot be stacked."
    if safe_text(placement.get("kind")) not in STACKABLE_KINDS:
        return False, "A whole drop-down cannot be stacked; it needs its full-height arrow."
    rows = placements_in(registry, placement.get("dest"))
    ids = [r.get("id") for r in rows]
    index = ids.index(placement_id)
    stack_id = safe_text(placement.get("stack"))
    next_index = index + 1
    while stack_id and next_index < len(rows) \
            and safe_text(rows[next_index].get("stack")) == stack_id:
        next_index += 1
    if next_index >= len(rows):
        return False, "There is no button below this one on the panel."
    other = rows[next_index]
    if is_marker(other) or safe_text(other.get("kind")) not in STACKABLE_KINDS:
        return False, "The row below is not a plain button."
    this_size = len([r for r in rows
                     if stack_id and safe_text(r.get("stack")) == stack_id]) or 1
    other_id = safe_text(other.get("stack"))
    other_size = len([r for r in rows
                      if other_id and safe_text(r.get("stack")) == other_id]) or 1
    if this_size + other_size > STACK_MAX:
        return False, "A stack holds at most {0} small buttons.".format(STACK_MAX)
    return True, ""


def group_with_next(registry, placement_id):
    """Stack this row with the one below it (two, then three).  The buttons
    render small; each keeps running its own command.  ``(ok, reason)``."""
    ok, reason = can_group_with_next(registry, placement_id)
    if not ok:
        return False, reason
    placement = find_placement_by_id(registry, placement_id)
    rows = placements_in(registry, placement.get("dest"))
    ids = [r.get("id") for r in rows]
    index = ids.index(placement_id)
    stack_id = safe_text(placement.get("stack"))
    next_index = index + 1
    while stack_id and next_index < len(rows) \
            and safe_text(rows[next_index].get("stack")) == stack_id:
        next_index += 1
    other = rows[next_index]
    other_id = safe_text(other.get("stack"))
    if stack_id:
        other["stack"] = stack_id
    elif other_id:
        placement["stack"] = other_id
    else:
        new_stack = new_id("k", _stack_ids(registry))
        placement["stack"] = new_stack
        other["stack"] = new_stack
    normalize_layout(registry)
    return True, ""


def ungroup(registry, node_id):
    """Dissolve the stack this row (or stack id) belongs to; the buttons stay
    where they are, full size again."""
    stack_id = safe_text(node_id)
    placement = find_placement_by_id(registry, node_id)
    if placement is not None:
        stack_id = safe_text(placement.get("stack"))
    if not stack_id:
        return False
    hit = False
    for row in registry.get("placements", []):
        if safe_text(row.get("stack")) == stack_id:
            row["stack"] = ""
            hit = True
    if hit:
        normalize_layout(registry)
    return hit


def _stack_ids(registry):
    return [safe_text(p.get("stack")) for p in registry.get("placements", [])
            if safe_text(p.get("stack"))]


def add_separator(registry, dest_id):
    """A vertical line at the end of the panel; move it like any row."""
    return _add_marker(registry, dest_id, "separator")


def add_slideout(registry, dest_id):
    """The panel's fold: rows below it drop into the slide-out that opens
    under the panel.  One per panel."""
    if has_slideout(registry, dest_id):
        return None
    return _add_marker(registry, dest_id, "slideout")


def has_slideout(registry, dest_id):
    return any(p.get("dest") == dest_id and safe_text(p.get("kind")) == "slideout"
               for p in registry.get("placements", []))


def _add_marker(registry, dest_id, kind):
    if find_destination_by_id(registry, dest_id) is None:
        return None
    orders = [p.get("order", 0) for p in registry.get("placements", [])
              if p.get("dest") == dest_id]
    entry = {
        "id": new_id("p", _ids(registry, "placements")),
        "source": "",
        "dest": dest_id,
        "order": (max(orders) + 1) if orders else 0,
        "kind": kind,
        "title": "",
        "control_id": "",
        "path": [],
        "stack": "",
    }
    registry.setdefault("placements", []).append(entry)
    return entry


def move_node(registry, node_kind, node_id, delta):
    """Up/Down for the tree.  A stack moves as one block, a plain row steps
    over a whole block, and a stacked member moves inside its stack - or out
    of it when it is already at the edge.  True when something moved."""
    if delta not in (-1, 1):
        return False
    if node_kind == "stack":
        return _move_block(registry, node_id, delta)
    placement = find_placement_by_id(registry, node_id)
    if placement is None:
        return False
    stack_id = safe_text(placement.get("stack"))
    if stack_id:
        return _move_member(registry, placement, stack_id, delta)
    return _move_plain(registry, placement, delta)


def _dest_blocks(registry, dest_id):
    """The panel's rows as blocks: a plain row alone, a stack's members
    together (assumes the normalized contiguous order)."""
    blocks = []
    seen = set()
    rows = placements_in(registry, dest_id)
    for row in rows:
        stack_id = safe_text(row.get("stack"))
        if not stack_id:
            blocks.append([row])
        elif stack_id not in seen:
            seen.add(stack_id)
            blocks.append([r for r in rows
                           if safe_text(r.get("stack")) == stack_id])
    return blocks


def _move_block(registry, stack_id, delta):
    dest_id = None
    for row in registry.get("placements", []):
        if safe_text(row.get("stack")) == safe_text(stack_id):
            dest_id = row.get("dest")
            break
    if dest_id is None:
        return False
    blocks = _dest_blocks(registry, dest_id)
    index = None
    for position, block in enumerate(blocks):
        if safe_text(block[0].get("stack")) == safe_text(stack_id):
            index = position
            break
    return _swap_blocks(registry, dest_id, blocks, index, delta)


def _move_plain(registry, placement, delta):
    blocks = _dest_blocks(registry, placement.get("dest"))
    index = None
    for position, block in enumerate(blocks):
        if len(block) == 1 and block[0].get("id") == placement.get("id"):
            index = position
            break
    return _swap_blocks(registry, placement.get("dest"), blocks, index, delta)


def _swap_blocks(registry, dest_id, blocks, index, delta):
    if index is None:
        return False
    target = index + delta
    if target < 0 or target >= len(blocks):
        return False
    blocks.insert(target, blocks.pop(index))
    order = 0
    for block in blocks:
        for row in block:
            row["order"] = order
            order += 1
    return True


def move_stack_to(registry, stack_id, dest_id):
    """Move a whole stack to another panel, still stacked."""
    if find_destination_by_id(registry, dest_id) is None:
        return False
    members = [r for r in registry.get("placements", [])
               if safe_text(r.get("stack")) == safe_text(stack_id)]
    if not members:
        return False
    members.sort(key=lambda r: (r.get("order", 0), r.get("id", "")))
    orders = [p.get("order", 0) for p in registry.get("placements", [])
              if p.get("dest") == dest_id]
    base = (max(orders) + 1) if orders else 0
    for offset, row in enumerate(members):
        row["dest"] = dest_id
        row["order"] = base + offset
    renumber(registry)
    return True


def _move_member(registry, placement, stack_id, delta):
    rows = [r for r in placements_in(registry, placement.get("dest"))
            if safe_text(r.get("stack")) == stack_id]
    ids = [r.get("id") for r in rows]
    index = ids.index(placement.get("id"))
    target = index + delta
    if 0 <= target < len(rows):
        other = rows[target]
        placement["order"], other["order"] = other["order"], placement["order"]
        normalize_layout(registry)
        return True
    # at the stack's edge the move steps out: the freed row keeps its order,
    # so it lands just above (or below) the block it left
    placement["stack"] = ""
    normalize_layout(registry)
    return True


def summarize(registry):
    return {
        "sources": len(registry.get("sources", [])),
        "destinations": len(registry.get("destinations", [])),
        "placements": len([p for p in registry.get("placements", [])
                           if not is_marker(p)]),
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
        entry = add_source(result, dict(source, id=None, extra_root=None,
                                        installed_by_my_ribbon=(source.get("kind") == "dynamo")))
        source_map[source.get("id")] = entry["id"]
        plan["sources_added"].append(entry.get("label") or entry.get("ext_name"))
    for source in result.get("sources", []):
        name = normalize_label(source.get("ext_name"))
        kind = source.get("kind")
        if kind in ("installed", "ribbon"):
            # A tab is only worth reporting when something could fetch it: a
            # pyRevit extension behind it named a URL when this was exported.
            if name and name not in installed and (kind == "installed" or source.get("url")):
                plan["sources_not_here"].append(source.get("label") or source.get("ext_name"))
        elif kind == "dynamo":
            # the graph path is checked by the host; nothing to install
            continue
        elif name not in installed:
            plan["sources_to_install"].append(source.get("label") or source.get("ext_name"))

    dest_map = {}
    new_dest_ids = set()
    for dest in incoming.get("destinations", []):
        existing = find_destination(result, dest.get("tab"), dest.get("panel"))
        if existing is None:
            entry = add_destination(result, dest.get("tab"), dest.get("panel"), dest.get("own_tab"))
            plan["destinations_added"].append("{0} > {1}".format(entry["tab"], entry["panel"]))
            new_dest_ids.add(entry["id"])
        else:
            entry = existing
        dest_map[dest.get("id")] = entry["id"]

    stack_map = {}
    for placement in sorted(incoming.get("placements", []),
                            key=lambda p: (p.get("dest", ""), p.get("order", 0))):
        dest_id = dest_map.get(placement.get("dest"))
        if is_marker(placement):
            # layout only: carried into panels this import creates; a panel
            # that already exists here keeps its own layout
            if dest_id in new_dest_ids:
                _add_marker(result, dest_id, safe_text(placement.get("kind")))
            continue
        source_id = source_map.get(placement.get("source"))
        if source_id is None or dest_id is None:
            plan["placements_skipped"].append(
                "{0} (its source or panel is missing from the file)".format(placement.get("title")))
            continue
        if find_placement(result, source_id, placement.get("path")) is not None:
            plan["placements_skipped"].append("{0} (already placed)".format(placement.get("title")))
            continue
        entry = add_placement(result, source_id, dest_id, placement)
        # stacks come along, under fresh ids so two registries never collide
        incoming_stack = safe_text(placement.get("stack"))
        if incoming_stack:
            if incoming_stack not in stack_map:
                stack_map[incoming_stack] = new_id(
                    "k", _stack_ids(result) + list(stack_map.values()))
            entry["stack"] = stack_map[incoming_stack]
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
        lines.append("Not installed here — My Ribbon will try to install from "
                     "pyRevit's catalogue: {0}.".format(", ".join(not_here)))
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
    ``python_engines`` (e.g. ``["CPython3"]``), ``engine`` (the one verdict of
    that list - see ``dynamo_engine``), ``run_type`` (``"Automatic"``,
    ``"Manual"``, ``"Periodic"`` or ``""`` when the file does not say),
    ``packages`` (names from the 2.x dependency list) and ``problem`` (a
    sentence when the file is not a runnable graph).  Never raises."""
    facts = {"format": "unknown", "is_custom_node": False, "name": "",
             "python_engines": [], "engine": "", "run_type": "",
             "packages": [], "problem": ""}
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
            view = data.get("View")
            if isinstance(view, dict) and isinstance(view.get("Dynamo"), dict):
                facts["run_type"] = safe_text(view["Dynamo"].get("RunType")).strip()
    elif raw.startswith("<"):
        if "<Workspace" in raw[:2000]:
            facts["format"] = "1.x"
            match = re.search(r'<Workspace[^>]*\sName="([^"]*)"', raw[:4000])
            if match:
                facts["name"] = match.group(1)
            if "PythonNode" in raw:
                facts["python_engines"] = ["IronPython2"]
            match = re.search(r'<Workspace[^>]*\sRunType="([^"]*)"', raw[:4000])
            if match:
                facts["run_type"] = match.group(1).strip()
    facts["engine"] = dynamo_engine(facts["python_engines"])
    if facts["format"] == "unknown" and not facts["problem"]:
        facts["problem"] = "This file does not look like a Dynamo graph (.dyn)."
    return facts


def dynamo_engine(python_engines):
    """One verdict from the per-node engine names: the single engine the graph
    uses, ``"mixed"`` when its Python nodes disagree, ``""`` when it has none.
    A Python node with no ``Engine`` key is already read as ``IronPython2``,
    which is what Dynamo falls back to."""
    names = [safe_text(name).strip() for name in (python_engines or [])]
    names = [name for name in names if name]
    if not names:
        return ""
    if len(set(names)) == 1:
        return names[0]
    return "mixed"


def dynamo_uses_cpython(facts):
    """True when any Python node runs on CPython (``CPython3``, ``PythonNet3``)."""
    for name in (facts or {}).get("python_engines") or []:
        lowered = safe_text(name).lower()
        if "cpython" in lowered or "pythonnet" in lowered:
            return True
    return False


def dynamo_needs_forced_run(facts):
    """True when the graph is saved in a run mode pyRevit will not execute.

    A pyRevit push-button opens the graph through Dynamo's journal interface,
    and a graph saved in Manual (or Periodic) run mode is opened and then never
    run - the click does nothing at all, with no UI and no error, because
    ``dynShowUI`` is false on a plain click.  Dynamo also re-saves a graph as
    Manual after a crash, so this is easy to hit without knowing.  The file
    says which mode it is in, so we can see it coming; ``force_automatic_run``
    is what fixes it."""
    run_type = safe_text((facts or {}).get("run_type")).strip().lower()
    return bool(run_type) and run_type != "automatic"


#: Matches the single ``RunType`` scalar of a 2.x graph, or the ``RunType``
#: attribute of a 1.x ``<Workspace>``; both spellings appear once in a graph.
_RUN_TYPE_JSON = re.compile(r'("RunType"\s*:\s*")([^"]*)(")')
_RUN_TYPE_XML = re.compile(r'(<Workspace[^>]*?\sRunType=")([^"]*)(")')


def force_automatic_run(text):
    """Return ``(patched_text, changed)`` - the same graph with its run mode set
    to Automatic, so pyRevit really runs it.  The substitution is textual and
    touches nothing but that one value, so the copy stays byte-for-byte the
    user's graph everywhere else; a file that does not carry exactly one
    ``RunType`` is handed back untouched rather than rewritten by guesswork.
    The user's own file is never the one being patched - only our copy."""
    raw = safe_text(text)
    for pattern in (_RUN_TYPE_JSON, _RUN_TYPE_XML):
        found = pattern.findall(raw)
        if len(found) != 1:
            continue
        if found[0][1].strip().lower() == "automatic":
            return raw, False
        return pattern.sub(lambda m: m.group(1) + "Automatic" + m.group(3), raw, count=1), True
    return raw, False


def dynamo_tags(facts):
    """Short labels for the picker/confirmation from ``dynamo_facts_from_text``."""
    tags = []
    if facts.get("format") == "1.x":
        tags.append("Dynamo 1.x graph")
    if facts.get("python_engines"):
        tags.append("contains Python nodes ({0})".format(", ".join(facts["python_engines"])))
    if dynamo_needs_forced_run(facts):
        tags.append("saved in {0} run mode - the button runs a copy set to Automatic, so edits "
                    "count from the next Apply".format(safe_text(facts.get("run_type"))))
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


def render_dynamo_bundle_yaml(title, tooltip, dynamo_path, clean=False):
    """The bundle.yaml of a Dynamo button.  Strings are JSON-quoted, which
    YAML reads as double-quoted scalars, so quotes, backslashes and newlines
    in titles or paths are safe (pyRevit reads YAML through YamlDotNet, whose
    scalars are always strings, which is why ``automate: true`` unquoted still
    satisfies its ``== 'true'`` test).

    ``dynamo_path`` is pyRevit's own key: the Dynamo engine runs that file
    instead of the bundle's script.dyn - so it is left out when the original is
    gone, and when the original is saved in a run mode pyRevit will not execute
    (then our patched copy is the one that must run).

    ``clean`` sets pyRevit's ``dynModelShutDown``, which tears down a Dynamo
    model left over from an earlier run before opening this graph.  It is asked
    for only by graphs with CPython nodes, whose evaluator is the one that
    fails - protected-memory crashes, "PythonEvaluator.Evaluate operation
    failed" - against a model already loaded beside pyRevit's own engine
    assemblies; pyRevit's own source notes the shutdown costs about 3x on
    start-up, which is why an IronPython graph does not pay it."""
    lines = [
        "title: " + json.dumps(safe_text(title), ensure_ascii=False),
        "tooltip: " + json.dumps(safe_text(tooltip), ensure_ascii=False),
        "author: \"EasyBIM My Ribbon\"",
        "engine:",
        "  automate: true",
    ]
    if clean:
        lines.append("  clean: true")
    if safe_text(dynamo_path):
        lines.append("  dynamo_path: " + json.dumps(safe_text(dynamo_path), ensure_ascii=False))
    return "\n".join(lines) + "\n"


def is_bundle_folder_name(name):
    """One plain ``<Name>.pushbutton`` folder name: no separators, no drive
    letter, not dots-only (same rule as ``easybim.my_ribbon``)."""
    name = safe_text(name)
    if not name or name != name.strip() or name in (".", ".."):
        return False
    if "/" in name or "\\" in name or ":" in name:
        return False
    return name.lower().endswith(".pushbutton") and len(name) > len(".pushbutton")


def unique_dynamo_bundles(registry, disk_names):
    """Give every Dynamo source a valid bundle name that no other source of
    the registry uses (an earlier source keeps its name; a clash, an invalid
    or missing name gets a fresh one that is also not on disk).  A renamed
    source has its placements' last path level and control id rewritten, since
    they embed the folder name.  Returns the list of (old, new) renames."""
    renames = []
    seen = set()
    disk = set(normalize_label(n) for n in (disk_names or []))
    for source in registry.get("sources", []):
        if source.get("kind") != "dynamo":
            continue
        name = safe_text(source.get("bundle"))
        if is_bundle_folder_name(name) and normalize_label(name) not in seen:
            seen.add(normalize_label(name))
            continue
        fresh = dynamo_bundle_name(source.get("title") or source.get("label"), list(seen | disk))
        seen.add(normalize_label(fresh))
        new_stem = strip_pushbutton(fresh)
        source["bundle"] = fresh
        for placement in registry.get("placements", []):
            if placement.get("source") != source.get("id"):
                continue
            path = placement.get("path") or []
            if path:
                path[-1]["name"] = new_stem
            placement["control_id"] = "CustomCtrl_%CustomCtrl_%{0}%{1}%{2}".format(
                DYNAMO_LIBRARY_TAB, DYNAMO_LIBRARY_PANEL, new_stem)
        renames.append((name, fresh))
    return renames


def strip_pushbutton(bundle_name):
    text = safe_text(bundle_name)
    if text.lower().endswith(".pushbutton"):
        return text[:-len(".pushbutton")]
    return text


#: Mirrors ``easybim.my_ribbon.LIBRARY_TAB`` / ``LIBRARY_DYNAMO_PANEL`` (pinned by a test).
DYNAMO_LIBRARY_TAB = "My Ribbon Library"
DYNAMO_LIBRARY_PANEL = "Dynamo"


def dynamo_tooltip(path, facts=None):
    parts = ["Dynamo graph: {0}".format(safe_text(path))]
    for tag in dynamo_tags(facts or {}):
        parts.append(tag[0].upper() + tag[1:])
    if (facts or {}).get("engine") in ("IronPython2", "mixed"):
        parts.append("IronPython2 nodes need Dynamo's DynamoIronPython2.7 package "
                     "on Dynamo 2.7 and newer.")
    parts.append("Ctrl+click opens it in Dynamo.")
    return "\n".join(parts)

