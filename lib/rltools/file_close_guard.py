# -*- coding: utf-8 -*-
"""Legacy compatibility wrapper for Close Stop runtime."""

from rltools import close_stop


def handle_command_before_exec(uiapp=None, event_args=None):
    """Forward old close-command capture calls to close_stop."""
    return close_stop.handle_command_before_exec(uiapp=uiapp, event_args=event_args)


def capture_action_command(uiapp=None, event_args=None):
    """Forward generic command capture calls to close_stop."""
    return close_stop.capture_action_command(uiapp=uiapp, event_args=event_args)


def handle_save_related_before_exec(uiapp=None, event_args=None):
    """Forward save/sync interception calls to close_stop."""
    return close_stop.handle_save_related_before_exec(uiapp=uiapp, event_args=event_args)


def handle_doc_closing(uiapp=None, event_args=None):
    """Forward old doc-closing calls to close_stop."""
    return close_stop.handle_doc_closing(uiapp=uiapp, event_args=event_args)


def handle_app_idling(uiapp=None, event_args=None):
    """Forward old idling calls to close_stop."""
    return close_stop.handle_app_idling(uiapp=uiapp, event_args=event_args)
