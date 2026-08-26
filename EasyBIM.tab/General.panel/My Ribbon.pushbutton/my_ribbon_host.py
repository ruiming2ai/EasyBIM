# -*- coding: utf-8 -*-
"""My Ribbon - the pyRevit/Revit side: folders, git, the parser, install, reload.

Everything external is injectable (``libgit``, ``parser``, ``requester``,
``user_config`` ...) so the fakes-driven test can drive each path without
Revit.  Nothing here edits the ribbon: that is ``easybim.my_ribbon``.
"""

from __future__ import print_function

import io
import json
import os
import shutil
import stat

from my_ribbon_state import (
    dynamo_bundle_name,
    dynamo_facts_from_text,
    dynamo_needs_forced_run,
    dynamo_tooltip,
    dynamo_uses_cpython,
    extension_dir_name,
    force_automatic_run,
    is_bundle_folder_name,
    normalize_label,
    render_dynamo_bundle_yaml,
    safe_text,
    strip_extension_suffix,
    unique_dynamo_bundles,
)

try:
    from easybim.my_ribbon import LIBRARY_EXTENSION_NAME, LIBRARY_TAB, LIBRARY_DYNAMO_PANEL
except Exception:
    # keep in step with lib/easybim/my_ribbon.py (pinned by a test)
    LIBRARY_EXTENSION_NAME = "EasyBIM_MyRibbon"
    LIBRARY_TAB = "My Ribbon Library"
    LIBRARY_DYNAMO_PANEL = "Dynamo"

#: The drawn Dynamo look-alike shipped with My Ribbon, used until Revit's own
#: Dynamo images are copied onto the shared item (or when Dynamo is absent).
DEFAULT_DYNAMO_ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dynamo_icon.png")
DEFAULT_DYNAMO_ICON_DARK = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "dynamo_icon.dark.png")


class CloneCancelled(Exception):
    """The user pressed Cancel on the download progress bar."""


class CloneAuthError(Exception):
    """The server wants credentials (private repository, wrong token)."""


# -- places -----------------------------------------------------------------


def extensions_root():
    """pyRevit's default third-party extension folder; always searched."""
    try:
        from pyrevit import THIRDPARTY_EXTENSIONS_DEFAULT_DIR
        if THIRDPARTY_EXTENSIONS_DEFAULT_DIR:
            return THIRDPARTY_EXTENSIONS_DEFAULT_DIR
    except Exception:
        pass
    return os.path.join(os.getenv("APPDATA") or "", "pyRevit", "Extensions")


def extension_roots():
    """Every folder pyRevit scans for extensions (default first)."""
    roots = []
    try:
        from pyrevit.userconfig import user_config
        roots = list(user_config.get_ext_root_dirs())
    except Exception:
        roots = []
    default = extensions_root()
    if default and default not in roots:
        roots.insert(0, default)
    return [r for r in roots if r]


def temp_clone_root():
    return os.path.join(extensions_root(), ".myribbon-tmp")


def repos_root():
    """Where repositories that are *not* themselves an extension go (a repo
    that holds ``X.extension`` sub-folders).  Machine-local: never roams."""
    base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or ""
    return os.path.join(base, "EasyBIM", "My Ribbon", "repos")


def host_version():
    try:
        from pyrevit import HOST_APP
        return safe_text(HOST_APP.version)
    except Exception:
        return ""


def existing_extension_dir_names(roots=None):
    names = []
    for root in (roots if roots is not None else extension_roots()):
        try:
            for entry in os.listdir(root):
                if os.path.isdir(os.path.join(root, entry)):
                    names.append(entry)
        except Exception:
            continue
    return names


def installed_extension_names(roots=None):
    """Names (folder minus ``.extension``) of every installed UI extension."""
    names = []
    for entry in existing_extension_dir_names(roots):
        if entry.lower().endswith(".extension"):
            names.append(strip_extension_suffix(entry))
    return names


def find_installed_extension_dir(ext_name, roots=None):
    wanted = normalize_label(strip_extension_suffix(ext_name)) + ".extension"
    for root in (roots if roots is not None else extension_roots()):
        try:
            for entry in os.listdir(root):
                if normalize_label(entry) == wanted and os.path.isdir(os.path.join(root, entry)):
                    return os.path.join(root, entry)
        except Exception:
            continue
    return None


# -- reachability -------------------------------------------------------------


def probe_url(web_url, timeout_seconds=5, requester=None):
    """Return ``(ok, status, message)`` after one quick HEAD request.

    A clone against an unreachable host can hang for a long time with no
    callback to cancel from, so this fails fast: offline, 404 (typo or a
    private repository on GitHub, which answers 404 for those), 401/403.
    """
    requester = requester or _dotnet_head_request
    try:
        status = int(requester(web_url, timeout_seconds))
    except Exception as ex:
        return False, 0, "Could not reach {0}: {1}".format(web_url, _short_error(ex))
    if status in (401, 403):
        return False, status, "The server refused access (HTTP {0}). The repository may be " \
                              "private - a username and access token may be needed.".format(status)
    if status == 404:
        return False, status, "Nothing was found at {0} (HTTP 404). Check the link; on GitHub a " \
                              "private repository also answers 404 until you sign in.".format(web_url)
    if status >= 400:
        return False, status, "The server answered HTTP {0} for {1}.".format(status, web_url)
    return True, status, ""


def _dotnet_head_request(url, timeout_seconds):
    import clr
    clr.AddReference("System")
    from System.Net import HttpWebRequest, WebException, ServicePointManager, SecurityProtocolType
    try:
        ServicePointManager.SecurityProtocol = (
            ServicePointManager.SecurityProtocol | SecurityProtocolType.Tls12)
    except Exception:
        pass
    request = HttpWebRequest.Create(url)
    request.Method = "HEAD"
    request.Timeout = int(timeout_seconds * 1000)
    request.AllowAutoRedirect = True
    request.UserAgent = "EasyBIM-MyRibbon"
    try:
        response = request.GetResponse()
        try:
            return int(response.StatusCode)
        finally:
            response.Close()
    except WebException as ex:
        response = getattr(ex, "Response", None)
        if response is not None:
            try:
                return int(response.StatusCode)
            finally:
                response.Close()
        raise


# -- git ----------------------------------------------------------------------


def _libgit():
    from pyrevit.coreutils import git as pygit
    return pygit.libgit


def clone_repository(clone_url, dest_dir, branch=None, username=None, password=None,
                     progress=None, libgit=None):
    """Clone into ``dest_dir`` (created by the clone).  ``progress(received,
    total)`` is called during the transfer and may return False to cancel.
    Raises ``CloneCancelled``, ``CloneAuthError`` or ``RuntimeError``."""
    libgit = libgit or _libgit()
    options = libgit.CloneOptions()
    if branch:
        _try_set(options, "BranchName", branch)
    _try_set(options, "RecurseSubmodules", True)
    if username and password:
        handler = _credentials_handler(libgit, username, password)
        if not _try_set(options, "CredentialsProvider", handler):
            fetch = getattr(options, "FetchOptions", None)
            if fetch is not None:
                _try_set(fetch, "CredentialsProvider", handler)
    cancel_flag = {"cancelled": False}
    if progress is not None:
        handler = _transfer_handler(libgit, progress, cancel_flag)
        if not _try_set(options, "OnTransferProgress", handler):
            fetch = getattr(options, "FetchOptions", None)
            if fetch is not None:
                _try_set(fetch, "OnTransferProgress", handler)
    try:
        libgit.Repository.Clone(clone_url, dest_dir, options)
    except Exception as ex:
        message = _short_error(ex)
        lowered = message.lower()
        # LibGit2Sharp's UserCancelledException carries libgit2's last error
        # text, not the word "cancel", so the callback's own flag decides.
        if cancel_flag["cancelled"] or "cancel" in lowered \
                or type(ex).__name__ == "UserCancelledException":
            raise CloneCancelled(message)
        if "401" in lowered or "403" in lowered or "authentication" in lowered \
                or "credential" in lowered or "too many redirects or authentication" in lowered:
            raise CloneAuthError(message)
        raise RuntimeError(message)
    return dest_dir


def _credentials_handler(libgit, username, password):
    def _handler(url, username_from_url, types):
        creds = libgit.UsernamePasswordCredentials()
        creds.Username = username
        creds.Password = password
        return creds
    try:
        return libgit.Handlers.CredentialsHandler(_handler)
    except Exception:
        return _handler


def _transfer_handler(libgit, progress, cancel_flag=None):
    def _handler(transfer):
        try:
            received = int(getattr(transfer, "ReceivedObjects", 0))
            total = int(getattr(transfer, "TotalObjects", 0))
        except Exception:
            received, total = 0, 0
        try:
            keep_going = progress(received, total)
        except Exception:
            keep_going = True
        if keep_going is False and cancel_flag is not None:
            cancel_flag["cancelled"] = True
        return keep_going is not False
    try:
        return libgit.Handlers.TransferProgressHandler(_handler)
    except Exception:
        return _handler


def clone_into_temp_then_move(clone_url, final_dir, temp_root, branch=None, username=None,
                              password=None, progress=None, libgit=None):
    """Clone under ``temp_root`` and rename into place only when complete, so
    a cancelled or failed download never leaves a half repository where
    pyRevit would try to load it."""
    if os.path.exists(final_dir):
        raise RuntimeError("The folder already exists: {0}".format(final_dir))
    if not os.path.isdir(temp_root):
        os.makedirs(temp_root)
    temp_dir = os.path.join(temp_root, os.path.basename(final_dir))
    remove_tree(temp_dir)
    try:
        clone_repository(clone_url, temp_dir, branch=branch, username=username,
                         password=password, progress=progress, libgit=libgit)
    except Exception:
        remove_tree(temp_dir)
        raise
    move_into_place(temp_dir, final_dir)
    return final_dir


def move_into_place(source_dir, final_dir):
    """Rename ``source_dir`` to ``final_dir``; falls back to a copy-and-delete
    move when the two live on different volumes (``%APPDATA%`` and
    ``%LOCALAPPDATA%`` can)."""
    if os.path.exists(final_dir):
        # shutil.move would nest the download *inside* an existing folder
        raise RuntimeError("The folder already exists: {0}".format(final_dir))
    parent = os.path.dirname(final_dir)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    try:
        os.rename(source_dir, final_dir)
    except OSError:
        shutil.move(source_dir, final_dir)
    return final_dir


def cleanup_temp_root(temp_root=None):
    """Delete leftovers of downloads that never finished."""
    temp_root = temp_root or temp_clone_root()
    if os.path.isdir(temp_root):
        remove_tree(temp_root)


def remote_url(repo_dir, libgit=None):
    """The origin URL of the git repository holding ``repo_dir`` (walks up)."""
    try:
        libgit = libgit or _libgit()
        found = libgit.Repository.Discover(repo_dir)
        if not found:
            return None
        repo = libgit.Repository(found)
        try:
            for remote in repo.Network.Remotes:
                if safe_text(getattr(remote, "Name", "")) == "origin":
                    return safe_text(remote.Url) or None
            for remote in repo.Network.Remotes:
                return safe_text(remote.Url) or None
        finally:
            _dispose(repo)
    except Exception:
        return None
    return None


def _dispose(obj):
    try:
        obj.Dispose()
    except Exception:
        pass


# -- repository layout -------------------------------------------------------


def find_extension_roots(clone_dir, max_depth=3):
    """What kind of thing was downloaded.

    Returns a dict: ``root_is_extension`` (the repository root itself holds
    ``*.tab`` folders, the pyRevit convention), ``extensions`` (paths of
    ``*.extension`` folders inside, up to ``max_depth`` deep), ``libs``
    (``*.lib`` folders beside them) and ``tab_roots`` (folders that hold
    ``*.tab`` sub-folders without the ``.extension`` name).
    """
    result = {"root_is_extension": False, "extensions": [], "libs": [], "tab_roots": []}
    if not clone_dir or not os.path.isdir(clone_dir):
        return result
    if _has_tab_dirs(clone_dir):
        result["root_is_extension"] = True
        result["tab_roots"].append(clone_dir)
    for folder, depth in _walk_dirs(clone_dir, max_depth):
        lowered = os.path.basename(folder).lower()
        if lowered.endswith(".extension"):
            result["extensions"].append(folder)
        elif lowered.endswith(".lib"):
            result["libs"].append(folder)
        elif depth > 0 and _has_tab_dirs(folder):
            result["tab_roots"].append(folder)
    return result


def _walk_dirs(root, max_depth):
    pending = [(root, 0)]
    while pending:
        folder, depth = pending.pop(0)
        if depth > 0:
            yield folder, depth
        if depth >= max_depth:
            continue
        try:
            entries = sorted(os.listdir(folder))
        except Exception:
            continue
        for entry in entries:
            if entry.startswith(".") or entry.startswith("_"):
                continue
            path = os.path.join(folder, entry)
            if os.path.isdir(path):
                pending.append((path, depth + 1))


def _has_tab_dirs(folder):
    try:
        entries = os.listdir(folder)
    except Exception:
        return False
    return any(e.lower().endswith(".tab") and os.path.isdir(os.path.join(folder, e))
               for e in entries)


def read_manifest_name(ext_dir):
    """The ``name`` from ``extension.json`` when the repository ships one."""
    path = os.path.join(ext_dir, "extension.json")
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, "r", encoding="utf-8-sig") as handle:
            data = json.loads(handle.read() or "{}")
        name = data.get("name") if isinstance(data, dict) else None
        return safe_text(name).strip() or None
    except Exception:
        return None


def plan_clone_destination(ref, temp_clone_dir, existing_names, extensions_dir=None,
                           repos_dir=None):
    """After a clone landed in the temp folder, decide where it lives.

    Returns a dict with ``kind``: ``"extension"`` (rename the whole clone to
    ``<Name>.extension`` under pyRevit's extensions folder - the pyRevit
    convention, so pyRevit's Update button sees it), ``"repository"`` (the
    repository holds ``*.extension`` sub-folders: keep the clone under
    ``repos_root()`` and register the folders holding those sub-folders as
    extra pyRevit roots; pyRevit's updater still finds the repository by
    walking up from each extension) or ``"none"``; plus ``final_dir``,
    ``ext_dirs`` (extension folders as they will be after the move),
    ``register_roots`` and ``message``.
    """
    extensions_dir = extensions_dir or extensions_root()
    repos_dir = repos_dir or repos_root()
    layout = find_extension_roots(temp_clone_dir)
    plan = {"kind": "none", "final_dir": None, "ext_dirs": [], "register_roots": [],
            "message": ""}
    if layout["root_is_extension"]:
        manifest = read_manifest_name(temp_clone_dir)
        name = extension_dir_name(ref, existing_names, manifest_name=manifest)
        final_dir = os.path.join(extensions_dir, name)
        plan.update({"kind": "extension", "final_dir": final_dir, "ext_dirs": [final_dir]})
        return plan
    folder_name = "{0}__{1}".format(ref.owner or "repo", ref.repo) if ref else "repo"
    final_dir = os.path.join(repos_dir, _safe_component(folder_name))
    if layout["extensions"]:
        ext_dirs = [os.path.join(final_dir, os.path.relpath(e, temp_clone_dir))
                    for e in layout["extensions"]]
        roots = []
        for ext_dir in ext_dirs:
            parent = os.path.dirname(ext_dir)
            if parent not in roots:
                roots.append(parent)
        plan.update({"kind": "repository", "final_dir": final_dir, "ext_dirs": ext_dirs,
                     "register_roots": roots})
        return plan
    if layout["tab_roots"]:
        # A folder with tabs but without the .extension name cannot be loaded
        # by pyRevit as-is; say so, so the user can ask the author.
        plan["message"] = (
            "This repository keeps its tools in a folder that is not named "
            "'<Something>.extension', so pyRevit cannot load it: {0}".format(
                ", ".join(os.path.relpath(t, temp_clone_dir) for t in layout["tab_roots"])))
        return plan
    plan["message"] = "No pyRevit tools were found in this repository."
    return plan


def _safe_component(text):
    return "".join(ch if (ch.isalnum() or ch in "._- ") else "_" for ch in safe_text(text)) or "repo"


# -- pyRevit parser adapter -----------------------------------------------------


def parse_extension_dir(ext_dir, parser=None):
    """Parse one extension folder with pyRevit's own parser and flatten it."""
    parser = parser or _pyrevit_parser()
    extension = parser.make_extension(ext_dir)
    return describe_extension(extension)


def _pyrevit_parser():
    from pyrevit.extensions.components import Extension
    from pyrevit.extensions.parser import get_parsed_extension

    class _Parser(object):
        @staticmethod
        def make_extension(ext_dir):
            extension = Extension(cmp_path=ext_dir)
            get_parsed_extension(extension)
            return extension

    return _Parser()


def describe_extension(extension):
    """Turn a parsed pyRevit ``Extension`` (or a fake with the same shape:
    ``name``, ``directory``, ``components``, ``startup_script``,
    ``hooks_path``, and per component ``name``/``ui_title``/``type_id``/
    ``components``/``icon_file``/``tooltip``/``control_id``/
    ``min_revit_ver``/``max_revit_ver``) into plain dicts."""
    tabs = []
    buttons = []
    for tab in _components(extension):
        if not safe_text(getattr(tab, "type_id", "")).endswith(".tab"):
            continue
        tab_level = _level(tab)
        panels = []
        for panel in _components(tab):
            if not safe_text(getattr(panel, "type_id", "")).endswith(".panel"):
                continue
            panel_level = _level(panel)
            shown = _shown_names(panel)
            items = []
            _collect_items(panel, [tab_level, panel_level], shown, items, buttons)
            panels.append({"name": panel_level["name"], "title": panel_level["title"],
                           "items": items})
        tabs.append({"name": tab_level["name"], "title": tab_level["title"], "panels": panels})
    startup = None
    try:
        startup = extension.startup_script
    except Exception:
        startup = None
    return {
        "name": safe_text(getattr(extension, "name", "")),
        "dir": safe_text(getattr(extension, "directory", "")),
        "tab_names": [t["name"] for t in tabs],
        "tabs": tabs,
        "buttons": buttons,
        "has_startup": bool(startup),
        "has_hooks": bool(getattr(extension, "hooks_path", None)),
    }


def _collect_items(container, path, shown, items, flat):
    for component in _components(container):
        type_id = safe_text(getattr(component, "type_id", "")).lower()
        kind = type_id.lstrip(".")
        if kind == "stack":
            # Stacks have no ribbon object of their own: their buttons sit
            # flat on the panel, so the path skips this level.  A stack's
            # own layout (rare) decides which of its children show.
            _collect_items(component, path, _shown_names(component), items, flat)
            continue
        level = _level(component)
        entry = {
            "kind": _kind(kind),
            "name": level["name"],
            "title": level["title"],
            "tooltip": _tooltip(component),
            "icon": safe_text(getattr(component, "icon_file", "")) or None,
            "control_id": safe_text(_getattr(component, "control_id")),
            "path": path + [level],
            "min_revit": safe_text(_getattr(component, "min_revit_ver")) or None,
            "max_revit": safe_text(_getattr(component, "max_revit_ver")) or None,
            "in_layout": (level["name"] in shown) if shown is not None else True,
            "children": [],
        }
        items.append(entry)
        flat.append(entry)
        if kind in ("pulldown", "splitbutton", "splitpushbutton"):
            child_shown = _shown_names(component)
            _collect_items(component, path + [level], child_shown, entry["children"], flat)


def _kind(kind):
    if kind in ("pulldown", "splitbutton", "splitpushbutton", "panelbutton",
                "nobutton", "combobox", "stack"):
        return kind
    return "button"


def _level(component):
    name = safe_text(getattr(component, "name", ""))
    title = safe_text(_getattr(component, "ui_title")) or name
    return {"name": name, "title": title}


def _components(container):
    components = getattr(container, "components", None)
    if components is None:
        try:
            return list(container)
        except Exception:
            return []
    return list(components)


def _shown_names(container):
    """Names of the children pyRevit will actually create (a ``layout:``
    hides the rest); None when there is no layout."""
    if not getattr(container, "layout_items", None):
        return None
    try:
        return set(safe_text(getattr(c, "name", "")) for c in list(container))
    except Exception:
        return None


def _tooltip(component):
    text = safe_text(_getattr(component, "tooltip"))
    return " ".join(text.split())


def _getattr(obj, name):
    try:
        return getattr(obj, name)
    except Exception:
        return None


# -- installed extensions and the catalogue -------------------------------------


def installed_extensions(extensionmgr=None):
    """Every installed UI extension, parsed and described.  Slow-ish (pyRevit
    parses them all); the caller shows a progress bar."""
    if extensionmgr is None:
        from pyrevit.extensions import extensionmgr as _mgr
        extensionmgr = _mgr
    described = []
    for extension in extensionmgr.get_installed_ui_extensions():
        try:
            described.append(describe_extension(extension))
        except Exception:
            continue
    return described


def catalogue_packages(extpackages=None, installed_names=None):
    """pyRevit's local ``extensions.json`` catalogue, UI extensions only."""
    if extpackages is None:
        from pyrevit.extensions import extpackages as _pkgs
        extpackages = _pkgs
    installed = set(normalize_label(n) for n in (installed_names or []))
    rows = []
    for package in extpackages.get_ext_packages():
        try:
            postfix = safe_text(getattr(getattr(package, "type", None), "POSTFIX", ""))
            if postfix and postfix != ".extension":
                continue
            name = safe_text(getattr(package, "name", ""))
            if not name:
                continue
            rows.append({
                "name": name,
                "url": safe_text(getattr(package, "url", "")),
                "description": " ".join(safe_text(getattr(package, "description", "")).split()),
                "author": safe_text(getattr(package, "author", "")),
                "builtin": bool(getattr(package, "builtin", False)),
                "installed": bool(getattr(package, "is_installed", "")) or normalize_label(name) in installed,
                "package": package,
            })
        except Exception:
            continue
    rows.sort(key=lambda r: normalize_label(r["name"]))
    return rows


def install_catalogue_package(package, root=None, extpackages=None):
    """Install a catalogue entry with pyRevit's own installer (dependencies
    included).  Returns the installed folder or raises."""
    if extpackages is None:
        from pyrevit.extensions import extpackages as _pkgs
        extpackages = _pkgs
    root = root or extensions_root()
    extpackages.install(package, root, install_dependencies=True)
    installed_dir = safe_text(getattr(package, "installed_dir", "")) or \
        os.path.join(root, safe_text(getattr(package, "ext_dirname", "")))
    return installed_dir


# -- extra extension roots (repositories that hold several extensions) ------------


def register_extension_root(path, user_config=None):
    """Tell pyRevit to scan ``path`` for extensions.  Returns ``(ok, msg)``."""
    try:
        if user_config is None:
            from pyrevit.userconfig import user_config as _cfg
            user_config = _cfg
        try:
            current = list(user_config.get_thirdparty_ext_root_dirs(include_default=False))
        except TypeError:
            current = [p for p in user_config.get_thirdparty_ext_root_dirs()
                       if normalize_label(p) != normalize_label(extensions_root())]
        if any(normalize_label(p) == normalize_label(path) for p in current):
            return True, ""
        user_config.set_thirdparty_ext_root_dirs(current + [path])
        user_config.save_changes()
        return True, ""
    except Exception as ex:
        return False, "pyRevit's settings could not be updated: {0}".format(_short_error(ex))


def unregister_extension_root(path, user_config=None):
    try:
        if user_config is None:
            from pyrevit.userconfig import user_config as _cfg
            user_config = _cfg
        try:
            current = list(user_config.get_thirdparty_ext_root_dirs(include_default=False))
        except TypeError:
            current = [p for p in user_config.get_thirdparty_ext_root_dirs()
                       if normalize_label(p) != normalize_label(extensions_root())]
        remaining = [p for p in current if normalize_label(p) != normalize_label(path)]
        if len(remaining) == len(current):
            return True, ""
        user_config.set_thirdparty_ext_root_dirs(remaining)
        user_config.save_changes()
        return True, ""
    except Exception as ex:
        return False, "pyRevit's settings could not be updated: {0}".format(_short_error(ex))


# -- removal ------------------------------------------------------------------


def _on_rm_error(func, path, exc_info):
    # git object files are read-only on Windows; make them writable and retry.
    del exc_info
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def remove_tree(path):
    if path and os.path.isdir(path):
        shutil.rmtree(path, onerror=_on_rm_error)


def remove_installed_source(source, allowed_roots=None):
    """Delete the folder My Ribbon installed for ``source``; never a folder
    the user installed themselves.  Returns ``(ok, message)``."""
    if not source.get("installed_by_my_ribbon"):
        return False, "This extension was not installed by My Ribbon, so it is left in place."
    if source.get("kind") == "dynamo":
        return delete_dynamo_bundle(source, root=(allowed_roots or [None])[0])
    roots = allowed_roots if allowed_roots is not None else [extensions_root(), repos_root()]
    if source.get("extra_root"):
        # a whole repository under repos_root(): delete the clone, not just
        # the registered sub-folder
        target = _repo_dir_for(source, roots)
    else:
        target = find_installed_extension_dir(source.get("ext_name"), roots[:1])
    if not target or not os.path.isdir(target):
        return True, ""
    if not any(_is_strictly_inside(target, root) for root in roots):
        return False, "Refusing to delete {0}: it is not a folder inside My Ribbon's own " \
                      "folders.".format(target)
    try:
        remove_tree(target)
    except Exception as ex:
        return False, "Could not delete {0}: {1}".format(target, _short_error(ex))
    if os.path.isdir(target):
        return False, "Could not delete {0} (files in use?).".format(target)
    return True, ""


def _repo_dir_for(source, roots):
    """The clone folder directly under a root that holds ``extra_root``; None
    when ``extra_root`` *is* a root or lies outside every root."""
    extra = source.get("extra_root") or ""
    for root in roots:
        if not _is_strictly_inside(extra, root):
            continue
        relative = os.path.relpath(extra, root)
        first = relative.replace("/", os.sep).split(os.sep)[0]
        if first in ("", ".", ".."):
            continue
        return os.path.join(root, first)
    return None


def _is_strictly_inside(path, root):
    """True when ``path`` is a folder *below* ``root`` - never root itself."""
    if not path or not root:
        return False
    try:
        path = os.path.normcase(os.path.abspath(path))
        root = os.path.normcase(os.path.abspath(root))
    except Exception:
        return False
    return path != root and path.startswith(root.rstrip(os.sep) + os.sep)


# -- My Ribbon's own extension: Dynamo buttons ------------------------------------


def library_extension_dir(root=None):
    return os.path.join(root or extensions_root(), LIBRARY_EXTENSION_NAME + ".extension")


def library_dynamo_panel_dir(root=None):
    return os.path.join(library_extension_dir(root), LIBRARY_TAB + ".tab",
                        LIBRARY_DYNAMO_PANEL + ".panel")


def ensure_library_extension(root=None):
    """Create the extension skeleton pyRevit loads (a tab and a panel folder)."""
    panel_dir = library_dynamo_panel_dir(root)
    if not os.path.isdir(panel_dir):
        os.makedirs(panel_dir)
    readme = os.path.join(library_extension_dir(root), "README.txt")
    if not os.path.isfile(readme):
        with io.open(readme, "w", encoding="utf-8") as handle:
            handle.write(u"Generated by EasyBIM My Ribbon. The buttons in here are rebuilt from "
                         u"My Ribbon's settings; edits are overwritten on Apply.\n")
    return library_extension_dir(root)


def dynamo_bundle_dir(source, root=None):
    """The bundle folder - only ever a plain name under the library panel; an
    invalid name maps to nothing rather than somewhere else on the disk."""
    name = safe_text(source.get("bundle"))
    if not is_bundle_folder_name(name):
        return None
    return os.path.join(library_dynamo_panel_dir(root), name)


def existing_dynamo_bundle_names(root=None):
    panel_dir = library_dynamo_panel_dir(root)
    try:
        return [n for n in os.listdir(panel_dir) if n.lower().endswith(".pushbutton")]
    except Exception:
        return []


def read_dynamo_facts(path):
    """Facts about a picked graph file (see ``dynamo_facts_from_text``)."""
    try:
        with io.open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            text = handle.read()
    except Exception as ex:
        facts = dynamo_facts_from_text("", os.path.basename(safe_text(path)))
        facts["problem"] = facts["problem"] or "The file could not be read: {0}".format(_short_error(ex))
        return facts
    return dynamo_facts_from_text(text, os.path.basename(safe_text(path)))


def new_dynamo_bundle_name(title, root=None):
    return dynamo_bundle_name(title, existing_dynamo_bundle_names(root))


def write_dynamo_bundle(source, facts=None, icon_png=None, root=None):
    """Write (or rewrite) the bundle for a Dynamo source: ``script.dyn`` (a copy
    of the graph - the fallback), ``bundle.yaml`` (title, tooltip and pyRevit's
    ``dynamo_path`` pointing at the original), and the icons (the user's PNG
    for both themes, else the drawn look-alikes).  Returns the bundle folder."""
    bundle = dynamo_bundle_dir(source, root)
    if bundle is None:
        raise ValueError("Not a bundle folder name: {0!r}".format(source.get("bundle")))
    ensure_library_extension(root)
    if not os.path.isdir(bundle):
        os.makedirs(bundle)
    refresh_dynamo_copy(source, root=root, facts=facts)
    yaml_text = desired_dynamo_yaml(source, facts)
    with io.open(os.path.join(bundle, "bundle.yaml"), "w", encoding="utf-8") as handle:
        handle.write(yaml_text if isinstance(yaml_text, type(u"")) else yaml_text.decode("utf-8"))
    write_dynamo_icons(source, icon_png=icon_png, root=root)
    return bundle


def desired_dynamo_yaml(source, facts=None):
    """What bundle.yaml must say for this source now.  ``dynamo_path`` names the
    original only while it exists *and* pyRevit would actually run it: when the
    graph is gone the key is left out so pyRevit runs the copy (with it set,
    pyRevit would try the missing file), and when the graph is saved in Manual
    or Periodic run mode it is left out too, so the run mode our copy forces to
    Automatic is the one that reaches Dynamo."""
    path = safe_text(source.get("path"))
    graph_exists = bool(path) and os.path.isfile(path)
    forced = dynamo_needs_forced_run(facts or {})
    tooltip = dynamo_tooltip(path, facts or {})
    if not graph_exists:
        tooltip += "\n(The original file is missing - the last copy runs.)"
    return render_dynamo_bundle_yaml(source.get("title") or source.get("label"), tooltip,
                                     path if (graph_exists and not forced) else None,
                                     clean=dynamo_uses_cpython(facts or {}))


def _read_text(path):
    try:
        with io.open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except Exception:
        return None


def write_dynamo_icons(source, icon_png=None, root=None):
    """The user's PNG (both themes) or the shipped look-alikes."""
    bundle = dynamo_bundle_dir(source, root)
    if bundle is None or not os.path.isdir(bundle):
        return False
    icon_png = icon_png or source.get("icon")
    if icon_png and os.path.isfile(icon_png):
        shutil.copyfile(icon_png, os.path.join(bundle, "icon.png"))
        shutil.copyfile(icon_png, os.path.join(bundle, "icon.dark.png"))
        return True
    wrote = False
    for name, default in (("icon.png", DEFAULT_DYNAMO_ICON), ("icon.dark.png", DEFAULT_DYNAMO_ICON_DARK)):
        if os.path.isfile(default):
            shutil.copyfile(default, os.path.join(bundle, name))
            wrote = True
    return wrote


def refresh_dynamo_copy(source, root=None, facts=None):
    """Keep ``script.dyn`` a copy of the original graph.  A graph saved in a run
    mode pyRevit will not execute is copied with that one value forced to
    Automatic (``force_automatic_run``), which is what makes the button run at
    all; the user's own file is never written.  Returns ``"copied"``,
    ``"current"`` (copy up to date), ``"missing"`` (original gone - the last
    copy stays) or ``"no-bundle"``."""
    bundle = dynamo_bundle_dir(source, root)
    if bundle is None or not os.path.isdir(bundle):
        return "no-bundle"
    path = safe_text(source.get("path"))
    target = os.path.join(bundle, "script.dyn")
    if not path or not os.path.isfile(path):
        return "missing"
    if facts is None:
        facts = read_dynamo_facts(path)
    patching = dynamo_needs_forced_run(facts)
    try:
        if os.path.isfile(target) and os.path.getmtime(target) >= os.path.getmtime(path) \
                and (patching or os.path.getsize(target) == os.path.getsize(path)):
            # a patched copy is deliberately a different size from the original,
            # so only its date can say whether it is still current
            return "current"
    except Exception:
        pass
    if patching and _write_forced_copy(path, target):
        return "copied"
    shutil.copyfile(path, target)
    try:
        shutil.copystat(path, target)
    except Exception:
        pass
    return "copied"


def _write_forced_copy(path, target):
    """Write ``target`` as ``path`` with its run mode forced to Automatic.
    False when the file cannot be read, decoded or patched, so the caller falls
    back to a plain copy rather than writing something half-understood."""
    try:
        with io.open(path, "rb") as handle:
            raw = handle.read()
    except Exception:
        return False
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
    except Exception:
        return False
    patched, changed = force_automatic_run(text)
    if not changed:
        return False
    try:
        with io.open(target, "wb") as handle:
            if bom:
                handle.write(b"\xef\xbb\xbf")
            handle.write(patched.encode("utf-8"))
    except Exception:
        return False
    try:
        shutil.copystat(path, target)
    except Exception:
        pass
    return True


def delete_dynamo_bundle(source, root=None):
    """Delete a Dynamo bundle - only ever a folder inside the library panel."""
    bundle = dynamo_bundle_dir(source, root)
    panel_dir = library_dynamo_panel_dir(root)
    if bundle is None or not _is_strictly_inside(bundle, panel_dir):
        return False, "Refusing to delete {0}: not a My Ribbon Dynamo bundle.".format(
            bundle or source.get("bundle"))
    if not os.path.isdir(bundle):
        return True, ""
    try:
        remove_tree(bundle)
    except Exception as ex:
        return False, "Could not delete {0}: {1}".format(bundle, _short_error(ex))
    if os.path.isdir(bundle):
        return False, "Could not delete {0} (files in use?).".format(bundle)
    return True, ""


def sync_dynamo_bundles(registry, pending_deletes=(), root=None):
    """Make the library match the registry: give every Dynamo source a unique
    valid bundle name (renaming its placements along), delete the bundles of
    removed sources, write bundles that are missing, rewrite a bundle.yaml
    that no longer says what the source says (a located graph, a missing one),
    refresh stale copies.  Mutates the registry; returns a report dict; never
    raises."""
    report = {"written": [], "refreshed": [], "current": [], "missing_graph": [],
              "deleted": [], "renamed": [], "errors": []}
    try:
        for old, new in unique_dynamo_bundles(registry, existing_dynamo_bundle_names(root)):
            report["renamed"].append((old, new))
    except Exception as ex:
        report["errors"].append("bundle names: {0}".format(_short_error(ex)))
    for source in pending_deletes or []:
        if source.get("kind") != "dynamo":
            continue
        bundle = dynamo_bundle_dir(source, root)
        existed = bool(bundle) and os.path.isdir(bundle)
        ok, message = delete_dynamo_bundle(source, root=root)
        if ok and existed:
            report["deleted"].append(source.get("label") or source.get("title"))
        elif not ok:
            report["errors"].append(message)
    for source in registry.get("sources", []):
        if source.get("kind") != "dynamo":
            continue
        try:
            bundle = dynamo_bundle_dir(source, root)
            if bundle is None:
                report["errors"].append("{0}: no bundle name".format(source.get("title")))
                continue
            graph_exists = bool(source.get("path")) and os.path.isfile(source.get("path"))
            has_copy = os.path.isfile(os.path.join(bundle, "script.dyn"))
            if not graph_exists and not has_copy:
                # no graph and no earlier copy: a bundle without script.dyn
                # would only make pyRevit log an error
                report["missing_graph"].append(source.get("id"))
                continue
            facts = read_dynamo_facts(source.get("path")) if graph_exists else {}
            yaml_path = os.path.join(bundle, "bundle.yaml")
            if not os.path.isdir(bundle) or not os.path.isfile(yaml_path):
                write_dynamo_bundle(source, facts=facts, root=root)
                report["written"].append(source.get("title") or source.get("label"))
            elif _read_text(yaml_path) != desired_dynamo_yaml(source, facts):
                # the graph was located elsewhere, renamed, or went missing
                write_dynamo_bundle(source, facts=facts, root=root)
                report["written"].append(source.get("title") or source.get("label"))
            else:
                if not os.path.isfile(os.path.join(bundle, "icon.png")):
                    write_dynamo_icons(source, root=root)
            status = refresh_dynamo_copy(source, root=root, facts=facts)
            if status == "copied":
                report["refreshed"].append(source.get("title") or source.get("label"))
            elif status == "missing":
                report["missing_graph"].append(source.get("id"))
            else:
                report["current"].append(source.get("title") or source.get("label"))
            # the copy is what pyRevit runs for a manual graph, so read it back:
            # a run mode we could not change is a button that will do nothing,
            # and the user has to hear that rather than find it out by clicking
            if graph_exists and dynamo_needs_forced_run(facts) and \
                    dynamo_needs_forced_run(read_dynamo_facts(os.path.join(bundle, "script.dyn"))):
                report["errors"].append(
                    "{0}: the graph is saved in {1} run mode, and My Ribbon could not set its "
                    "copy to Automatic. Open the graph in Dynamo and set Run to Automatic, or "
                    "the button will do nothing.".format(
                        source.get("title") or source.get("label"),
                        safe_text(facts.get("run_type")) or "Manual"))
        except Exception as ex:
            report["errors"].append("{0}: {1}".format(source.get("title") or source.get("label"),
                                                      _short_error(ex)))
    return report


def dynamo_graph_status(source, root=None):
    """``"missing"`` (original gone - Locate it), ``"no-bundle"`` (needs Apply)
    or ``"ok"``."""
    path = safe_text(source.get("path"))
    if not path or not os.path.isfile(path):
        return "missing"
    bundle = dynamo_bundle_dir(source, root)
    if bundle is None or not os.path.isdir(bundle):
        return "no-bundle"
    return "ok"


# -- reload / explorer ---------------------------------------------------------


def reload_pyrevit():
    from pyrevit.loader import sessionmgr
    sessionmgr.reload_pyrevit()


def show_folder(path):
    try:
        from pyrevit import script
        script.show_folder_in_explorer(path)
        return True
    except Exception:
        pass
    try:
        os.startfile(path)  # pylint: disable=no-member
        return True
    except Exception:
        return False


def _short_error(ex):
    text = safe_text(ex)
    inner = getattr(ex, "InnerException", None)
    if inner is not None:
        text = "{0} ({1})".format(text, safe_text(inner))
    return " ".join(text.split())[:400]


def _try_set(obj, name, value):
    try:
        setattr(obj, name, value)
        return True
    except Exception:
        return False
