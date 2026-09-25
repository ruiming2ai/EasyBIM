# -*- coding: utf-8 -*-
"""Resolve caption-to-color mappings without guessing ambiguous tabs."""

from __future__ import absolute_import


def build_caption_assignments(entries):
    """Return (safe assignments, conflicts) keyed by exact caption.

    A caption is safe only when every DB.View candidate carrying that caption
    resolves to the same color. A disabled category participates as None, so a
    visible title shared by a colored and intentionally uncolored view is left
    unchanged.
    """
    buckets = {}
    for entry in entries:
        for caption in entry.get("captions", []):
            if not caption:
                continue
            buckets.setdefault(caption, []).append(entry)

    assignments = {}
    conflicts = {}
    for caption, candidates in buckets.items():
        colors = set(candidate.get("color") for candidate in candidates)
        if len(colors) == 1:
            only_color = list(colors)[0]
            if only_color:
                assignments[caption] = {
                    "color": only_color,
                    "entries": candidates,
                }
        else:
            conflicts[caption] = candidates
    return assignments, conflicts


def _caption_exclusions(assignments, entries=None):
    """Return longer, differently colored captions to exclude per token.

    pyRevit evaluates title filters against AvalonDock's internal title. That
    title can contain document/type text around the visible Revit caption, so
    a useful rule must find the API-derived caption *inside* that string.

    A short caption may be present inside a longer caption of another color.
    Keep the short token, but guard its rule against those longer titles.
    Dropping it entirely makes a standalone view lose its color when view
    activation adds an inferred host sheet whose title contains that name.
    Disabled and ambiguous captions must also block the shorter rule.
    """
    result = {}
    items = list(assignments.items())
    competing_tokens = [
        (caption, assignment["color"]) for caption, assignment in items
    ]
    for entry in entries or []:
        for caption in entry.get("captions", []):
            if caption:
                competing_tokens.append((caption, entry.get("color")))
    for caption, assignment in items:
        color = assignment["color"]
        exclusions = set()
        for other_caption, other_color in competing_tokens:
            if caption == other_caption:
                continue
            if color != other_color and caption in other_caption:
                exclusions.add(other_caption)
        result[caption] = sorted(exclusions, key=lambda item: (-len(item), item))
    return result


def _caption_fragment(caption, escape_function):
    """Build a literal contained-title match with conservative word guards."""
    return r"(?<!\w){0}(?!\w)".format(escape_function(caption))


def _guarded_caption_fragment(caption, exclusions, escape_function):
    fragment = _caption_fragment(caption, escape_function)
    if not exclusions:
        return fragment
    competitors = "|".join(
        _caption_fragment(other, escape_function) for other in exclusions
    )
    # Test the whole internal title from its start: a sheet number can precede
    # the view name, so a lookahead at the short token alone is insufficient.
    # Both .NET Regex and Python support these guards. [\s\S] also covers
    # multiline document titles without depending on external regex flags.
    return r"\A(?![\s\S]*(?:{0}))[\s\S]*?{1}".format(competitors, fragment)


def build_filter_rules(
    assignments,
    escape_function,
    max_pattern_length=16000,
    entries=None,
):
    """Group safe, escaped caption tokens into compact pyRevit filters.

    Revit/pyRevit versions can pass an internal title such as
    ``<document> - <view title>`` even when only the view name is visible.
    Caption tokens can match anywhere in that title, unless it contains a
    competing longer caption. Word guards keep ``Level 1`` out of ``Level 10``.
    """
    by_color = {}
    exclusions = _caption_exclusions(assignments, entries=entries)
    for caption in exclusions:
        assignment = assignments[caption]
        by_color.setdefault(assignment["color"], []).append(caption)

    rules = []
    for color in sorted(by_color.keys()):
        captions = sorted(
            set(by_color[color]), key=lambda item: (-len(item), item)
        )
        chunk = []
        chunk_length = 0
        for caption in captions:
            fragment = _guarded_caption_fragment(
                caption, exclusions[caption], escape_function
            )
            added_length = len(fragment) + (1 if chunk else 0)
            if chunk and chunk_length + added_length > max_pattern_length:
                rules.append(_make_rule(color, chunk))
                chunk = []
                chunk_length = 0
            chunk.append(fragment)
            chunk_length += added_length
        if chunk:
            rules.append(_make_rule(color, chunk))
    return rules


def _make_rule(color, caption_fragments):
    return {
        "color": color,
        "pattern": r"(?#ViewTabColors)(?:{0})".format(
            "|".join(caption_fragments)
        ),
        "caption_count": len(caption_fragments),
    }
