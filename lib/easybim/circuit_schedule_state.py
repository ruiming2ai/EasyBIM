# -*- coding: utf-8 -*-
"""Pure tree logic for Circuit Schedule.

The model is scanned once into plain dicts by ``circuit_schedule_revit``; every
decision about what the browser shows is made here, off those dicts, so the
whole hierarchy - roots, nesting, the loop guard, the search - is testable on
the desktop without Revit.

Deliberately free of ``easybim`` imports.  ``compat.py`` records that the
``*_state`` companions keep local copies of anything they need precisely so the
test suite can load them standalone with ``spec_from_file_location``.

Search filters in place, by flipping ``is_visible`` on the existing nodes,
rather than by rebuilding a pruned tree.  Rebuilding would hand WPF new objects
on every keystroke and drop the selection - and the selection is what the
breadcrumb is drawn from.
"""

from __future__ import print_function


NODE_EQUIPMENT = "equipment"   # a panel, switchboard or transformer
NODE_CIRCUIT = "circuit"       # one ElectricalSystem
NODE_LOAD = "load"             # an end device sitting on a circuit
NODE_BUCKET = "bucket"         # a heading for circuits that hang off nothing

UNASSIGNED_TITLE = u"Unassigned circuits"
UNASSIGNED_SUBTITLE = u"no panel assigned - these feed from nowhere"

ROOT_SUBTITLE = u"utility service - nothing upstream"
CYCLE_SUBTITLE = u"circular feed - this board is already upstream of itself"
REFERENCE_SUBTITLE = u"already shown under {0}"
SPARE_DETAIL = u"spare"

#: One glyph per row kind.  The circuit glyph points down on purpose: a
#: circuit is always the step *out of* the board above it, so the tree reads
#: board - down - board - down - device without the user learning a legend.
GLYPHS = {
    NODE_EQUIPMENT: u"\u25a3",   # a board
    NODE_CIRCUIT: u"\u25bc",     # down to what it feeds
    NODE_LOAD: u"\u25aa",        # an end device
    NODE_BUCKET: u"\u25c7",
}

#: Dark at the service, pale far downstream.  Applied to the row glyph and its
#: left rule only - never to the body text, which has to stay legible in both
#: Revit themes.
DEPTH_ACCENTS = (u"#1F4E79", u"#2E75B6", u"#4E9BD5", u"#7BB6E4", u"#A9CCE9")

#: Belt and braces behind the loop guard.  A guard bug must cost a truncated
#: branch, never a hung Revit.
MAX_DEPTH = 64

#: Everything a circuit number is written with.  ``12,14,16`` is one circuit.
_SEPARATORS = u",-/\;:+&"


# ------------------------------------------------------------------- tokens


def accent_for_depth(depth):
    """Clamps: a ten-deep model still gets a colour, just the palest one."""
    if depth < 0:
        depth = 0
    if depth >= len(DEPTH_ACCENTS):
        depth = len(DEPTH_ACCENTS) - 1
    return DEPTH_ACCENTS[depth]


def _norm(text):
    return (text or u"").strip().lower()


def tokens(text):
    """``"LP-1/12"`` -> ``["lp", "1", "12"]``.

    Splitting rather than substring-matching is what stops a search for
    circuit ``12`` from dragging in every ``112`` and ``120`` in the project.
    """
    out = []
    current = u""
    for char in _norm(text):
        if char.isspace() or char in _SEPARATORS:
            if current:
                out.append(current)
            current = u""
        else:
            current += char
    if current:
        out.append(current)
    return out


def _leading_number(text):
    """``"12A"`` -> ``12.0``; ``"A"`` -> ``None``."""
    digits = u""
    for char in text or u"":
        if char.isdigit():
            digits += char
        elif digits:
            break
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def circuit_number_sort_key(number):
    """Order circuit numbers the way a panel schedule does: 1, 2, 10 - not 1, 10, 2."""
    text = _norm(number)
    numeric = _leading_number(text)
    return (0 if numeric is not None else 1, numeric or 0.0, text)


def name_sort_key(name):
    """Natural order for panel names, so ``LP-2`` precedes ``LP-10``."""
    parts = []
    for token in tokens(name):
        numeric = _leading_number(token)
        if numeric is not None and token.isdigit():
            parts.append((0, numeric, u""))
        else:
            parts.append((1, 0.0, token))
    return parts


def _split_panel_number(query):
    """``"LP-1/12"`` -> ``("lp-1", "12")``; anything else -> ``(None, None)``.

    Only the *last* separator is a candidate, and only when what follows it
    reads as a circuit number.  ``LP-1`` on its own must stay a panel search.
    """
    text = _norm(query)
    for index in range(len(text) - 1, 0, -1):
        char = text[index]
        if not (char.isspace() or char in u"/"):
            continue
        head = text[:index].strip()
        tail = text[index + 1:].strip()
        if head and tail and _leading_number(tail) is not None:
            return head, tail
        return None, None
    return None, None


# -------------------------------------------------------------------- nodes


class Node(object):
    """One row of the browser.

    Plain object.  ``circuit_schedule_panel`` subclasses it to add WPF change
    notification for the three fields the user moves - ``is_expanded``,
    ``is_visible`` and ``matched`` - the same way ``circuit_rating_ui.CircuitRow``
    layers notification onto ``CircuitRowBase``.
    """

    def __init__(self, node_kind, key, title, subtitle=u"", detail=u"",
                 element_ids=None, depth=0):
        self.node_kind = node_kind
        self.key = key
        self.title = title or u""
        self.subtitle = subtitle or u""
        self.detail = detail or u""
        self.element_ids = list(element_ids or [])
        self.depth = depth
        self.glyph = GLYPHS.get(node_kind, u"")
        self.accent = accent_for_depth(depth)
        #: Short form for the breadcrumb.  A circuit contributes its number
        #: alone, so the chain reads ``MSB > 3 > LP-1 > 12 > Recept`` rather
        #: than repeating each circuit's load name back at the user.
        self.crumb = self.title
        #: The circuit number, in its own column.  Only circuits carry one, and
        #: giving it a column rather than a prefix is what lets the numbers line
        #: up down the pane the way a panel schedule reads.
        self.number_text = u''
        self.children = []
        self.is_expanded = depth < 1
        self.is_visible = True
        #: This board is already upstream of itself - a modelling error.
        self.is_cycle = False
        #: This board is legitimately fed twice; its subtree is drawn under the
        #: first feeder and this row points at it rather than repeating it.
        self.is_reference = False
        self.matched = False
        #: Substring haystack - names and categories only.  Numeric readings
        #: (ratings, voltages, VA) are deliberately left out: a search for
        #: circuit "12" must not land on a 1200 VA load.
        self.match_text = u""
        #: Exact-token haystack, for circuit numbers and element ids.
        self.number_tokens = set()

    @property
    def has_children(self):
        return bool(self.children)

    def notify(self, attr):
        """Overridden by the WPF subclass; a no-op off Revit."""

    def add(self, child):
        self.children.append(child)
        return child

    def __repr__(self):
        return "<Node {0} {1!r}>".format(self.node_kind, self.title)


def _set(node, attr, value):
    if getattr(node, attr) == value:
        return False
    setattr(node, attr, value)
    node.notify(attr)
    return True


def _make(node_factory, *args, **kwargs):
    factory = node_factory or Node
    return factory(*args, **kwargs)


# ----------------------------------------------------------------- indexing


def _element(snapshot, element_id):
    return (snapshot.get("elements") or {}).get(element_id)


def _element_text(element):
    if not element:
        return u""
    return u" ".join(part for part in (
        element.get("name"), element.get("type_name"),
        element.get("category"), element.get("level")) if part)


def _circuit_label(circuit):
    number = circuit.get("circuit_number") or u"?"
    panel = circuit.get("panel_name") or u""
    return u"{0} / {1}".format(panel, number) if panel else number


def _circuit_detail(circuit):
    """``20 A - 1P - 120 V - 1440 VA`` - only the parts that were read."""
    parts = []
    for key, suffix in (("rating", u""), ("poles", u"P"),
                        ("voltage", u""), ("apparent_load", u"")):
        value = circuit.get(key)
        if value:
            parts.append(u"{0}{1}".format(value, suffix))
    return u" · ".join(parts)


# ------------------------------------------------------------ tree building


def build_tree(snapshot, node_factory=None):
    """``snapshot`` -> ``(roots, index, parents)``.

    ``roots`` are the pieces of equipment nothing feeds - the service entrance
    and any switchboard modelled without a feeder - followed by an
    ``Unassigned circuits`` bucket when there are circuits with no panel.
    """
    snapshot = snapshot or {}
    elements = snapshot.get("elements") or {}
    circuits = list(snapshot.get("circuits") or [])

    circuits_by_panel = {}
    fed_ids = set()
    unassigned = []
    for circuit in circuits:
        panel_id = circuit.get("panel_id")
        if panel_id is None or panel_id not in elements:
            unassigned.append(circuit)
        else:
            circuits_by_panel.setdefault(panel_id, []).append(circuit)
        for element_id in circuit.get("element_ids") or []:
            fed_ids.add(element_id)

    for panel_id in circuits_by_panel:
        circuits_by_panel[panel_id].sort(
            key=lambda item: circuit_number_sort_key(item.get("circuit_number")))

    root_ids = [element_id for element_id in elements
                if element_id not in fed_ids and element_id in circuits_by_panel]
    root_ids.sort(key=lambda element_id: name_sort_key(
        (elements.get(element_id) or {}).get("name")))

    # ``drawn`` is global to the whole build: it remembers which boards already
    # own a full subtree, so a legitimately double-fed board is expanded once
    # and merely pointed at from its second feeder.
    drawn = {}
    roots = []
    for element_id in root_ids:
        roots.append(_equipment_node(
            snapshot, circuits_by_panel, element_id, None, 0, set(), drawn,
            node_factory))

    if unassigned:
        bucket = _make(node_factory, NODE_BUCKET, "bucket:unassigned",
                       UNASSIGNED_TITLE, UNASSIGNED_SUBTITLE,
                       u"{0} circuit(s)".format(len(unassigned)), None, 0)
        bucket.match_text = _norm(UNASSIGNED_TITLE)
        unassigned.sort(
            key=lambda item: circuit_number_sort_key(item.get("circuit_number")))
        for circuit in unassigned:
            bucket.add(_circuit_node(
                snapshot, circuits_by_panel, circuit, 1, set(), drawn,
                node_factory))
        roots.append(bucket)

    index, parents = build_index(roots)
    return roots, index, parents


def _equipment_node(snapshot, circuits_by_panel, element_id, feeder, depth,
                    path, drawn, node_factory):
    element = _element(snapshot, element_id) or {}
    title = element.get("name") or u"Equipment"
    key = "eq:{0}".format(element_id) if feeder is None else \
        "eq:{0}@{1}".format(element_id, feeder.get("id"))

    def stub(subtitle, flag):
        node = _make(node_factory, NODE_EQUIPMENT, key, title, subtitle, u"",
                     [element_id], depth)
        setattr(node, flag, True)
        node.match_text = _norm(_element_text(element))
        node.number_tokens = set([str(element_id)])
        return node

    if element_id in path:
        # A feeds B feeds A.  Stop rather than recurse forever, and say why -
        # a loop is a modelling error the user needs to see, not one to hide.
        return stub(CYCLE_SUBTITLE, "is_cycle")
    if depth >= MAX_DEPTH:
        return stub(CYCLE_SUBTITLE, "is_cycle")
    if element_id in drawn:
        # A double-ended board really is fed twice.  Both feeders must show it,
        # but drawing the subtree twice doubles the tree for no new information.
        return stub(REFERENCE_SUBTITLE.format(drawn[element_id]), "is_reference")

    subtitle = ROOT_SUBTITLE if feeder is None else \
        u"fed from {0}".format(_circuit_label(feeder))
    own_circuits = circuits_by_panel.get(element_id) or []
    node = _make(node_factory, NODE_EQUIPMENT, key, title, subtitle,
                 u"{0} circuit(s)".format(len(own_circuits)), [element_id], depth)
    node.match_text = _norm(_element_text(element))
    node.number_tokens = set([str(element_id)])
    drawn[element_id] = _circuit_label(feeder) if feeder else title

    branch = set(path)
    branch.add(element_id)
    for circuit in own_circuits:
        node.add(_circuit_node(snapshot, circuits_by_panel, circuit, depth + 1,
                               branch, drawn, node_factory))
    return node


def _circuit_node(snapshot, circuits_by_panel, circuit, depth, path, drawn,
                  node_factory):
    number = circuit.get("circuit_number") or u"?"
    load_name = circuit.get("load_name") or u""
    element_ids = list(circuit.get("element_ids") or [])

    title = load_name or u"Circuit {0}".format(number)
    subtitle = _circuit_detail(circuit)
    detail = SPARE_DETAIL if not element_ids else \
        u"{0} load(s)".format(len(element_ids))

    node = _make(node_factory, NODE_CIRCUIT, "ckt:{0}".format(circuit.get("id")),
                 title, subtitle, detail, element_ids, depth)
    node.crumb = number
    node.number_text = number
    node.match_text = _norm(u" ".join(part for part in (
        load_name, circuit.get("panel_name"), circuit.get("system_type")) if part))
    node.number_tokens = set(tokens(number))
    node.number_tokens.add(str(circuit.get("id")))

    for element_id in element_ids:
        element = _element(snapshot, element_id) or {}
        if element.get("is_panel"):
            node.add(_equipment_node(
                snapshot, circuits_by_panel, element_id, circuit, depth + 1,
                path, drawn, node_factory))
        else:
            node.add(_load_node(element, element_id, circuit, depth + 1,
                                node_factory))
    return node


def _load_node(element, element_id, circuit, depth, node_factory):
    title = element.get("name") or element.get("type_name") or u"Element"
    subtitle = u" · ".join(part for part in (
        element.get("category"), element.get("level")) if part)
    node = _make(node_factory, NODE_LOAD,
                 "load:{0}@{1}".format(element_id, circuit.get("id")),
                 title, subtitle, u"[{0}]".format(element_id), [element_id], depth)
    node.match_text = _norm(_element_text(element))
    node.number_tokens = set([str(element_id)])
    return node


def build_index(roots):
    """``({key: node}, {key: parent node})`` - the parent map feeds the breadcrumb."""
    index = {}
    parents = {}

    def walk(node, parent):
        index[node.key] = node
        parents[node.key] = parent
        for child in node.children:
            walk(child, node)

    for root in roots or []:
        walk(root, None)
    return index, parents


def iter_nodes(roots):
    stack = list(roots or [])
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


def breadcrumb(node, parents, separator=u"  ▸  "):
    """The full upstream path of ``node``, root first.

    This is what keeps "upstream" legible after a search drops the user six
    levels down with every ancestor scrolled off the top of the pane.
    """
    if node is None:
        return u""
    chain = []
    current = node
    seen = set()
    while current is not None and current.key not in seen:
        seen.add(current.key)
        chain.append(current.crumb)
        current = (parents or {}).get(current.key)
    chain.reverse()
    return separator.join(part for part in chain if part)


# ------------------------------------------------------------------- search


def node_matches(node, query):
    """Does this row answer ``query``?

    Three ways, in order of how specific they are:

    1. ``LP-1/12`` - a panel and a circuit number together.
    2. Every token of the query is a circuit-number (or element id) token of
       this row.  ``12`` finds circuit ``12`` and ``12,14,16``; it does *not*
       find ``112``, which is the whole point of tokenising.
    3. Plain substring over names and categories.
    """
    text = _norm(query)
    if not text:
        return False

    head, tail = _split_panel_number(text)
    if head is not None:
        if head in node.match_text and _is_subset(tokens(tail), node.number_tokens):
            return True

    query_tokens = tokens(text)
    if node.number_tokens and query_tokens and \
            _is_subset(query_tokens, node.number_tokens):
        return True

    return text in node.match_text


def _is_subset(query_tokens, node_tokens):
    if not query_tokens:
        return False
    for token in query_tokens:
        if token not in node_tokens:
            return False
    return True


def apply_filter(roots, query, default_depth=1):
    """Show only the branches that answer ``query``.  Returns the match count.

    A row that matches keeps its whole subtree - searching for a circuit is
    asking what is *on* it - and every ancestor stays visible and expanded so
    the row is reachable.  An empty query restores the full tree and collapses
    it back to ``default_depth``.
    """
    text = _norm(query)
    if not text:
        for node in iter_nodes(roots):
            _set(node, "is_visible", True)
            _set(node, "matched", False)
            _set(node, "is_expanded", node.depth < default_depth)
        return 0

    total = [0]

    def walk(node, forced):
        """Returns True when this row, or something under it, actually matched."""
        matched = node_matches(node, text)
        if matched:
            total[0] += 1
        _set(node, "matched", matched)

        hit_below = False
        for child in node.children:
            if walk(child, forced or matched):
                hit_below = True

        _set(node, "is_visible", bool(matched or hit_below or forced))
        # Only a real hit below is worth opening.  A matched panel keeps its
        # whole subtree reachable but stays closed: expanding it would bury the
        # hit under every circuit and load it owns.
        _set(node, "is_expanded", hit_below)
        return matched or hit_below

    for root in roots or []:
        walk(root, False)
    return total[0]


def set_expanded(roots, value, max_depth=None):
    """Expand or collapse the tree; ``max_depth`` opens only down to that level."""
    for node in iter_nodes(roots):
        if not node.children:
            continue
        if value and max_depth is not None and node.depth > max_depth:
            _set(node, "is_expanded", False)
        else:
            _set(node, "is_expanded", bool(value))


def expansion_state(roots):
    """``{key: is_expanded}`` for the rows that have children."""
    return dict((node.key, node.is_expanded)
                for node in iter_nodes(roots) if node.children)


def restore_expansion(roots, state):
    """Put a saved expansion back after a Refresh rebuilt the tree."""
    if not state:
        return
    for node in iter_nodes(roots):
        if node.key in state:
            _set(node, "is_expanded", state[node.key])


def summarize(snapshot, roots):
    """The status line: what the model holds, and what could not be placed."""
    circuits = list((snapshot or {}).get("circuits") or [])
    panels = set()
    unassigned = 0
    for circuit in circuits:
        panel_id = circuit.get("panel_id")
        if panel_id is None:
            unassigned += 1
        else:
            panels.add(panel_id)
    parts = [u"{0} circuit(s)".format(len(circuits)),
             u"{0} panel(s)".format(len(panels))]
    if unassigned:
        parts.append(u"{0} unassigned".format(unassigned))
    loops = sum(1 for node in iter_nodes(roots) if node.is_cycle)
    if loops:
        parts.append(u"{0} loop(s)".format(loops))
    return u"  ·  ".join(parts)
