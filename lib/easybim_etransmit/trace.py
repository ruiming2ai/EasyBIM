# -*- coding: utf-8 -*-
"""Crash-boundary breadcrumbs for e-transmit."""
from __future__ import unicode_literals
import datetime
import io
import os

try:
    text = unicode
except NameError:
    text = str


def write(uiapp, label, root=None, detail=''):
    message = 'EasyBIM e-transmit {0}'.format(text(label or '').strip())
    if detail:
        message += ' | ' + text(detail).replace('\r', ' ').replace('\n', ' ')

    try:
        application = getattr(uiapp, 'Application', None)
        writer = getattr(application, 'WriteJournalComment', None)
        if writer is not None:
            writer(message, False)
    except Exception:
        pass

    try:
        from pyrevit import script
        script.get_logger().info(message)
    except Exception:
        pass

    if not root:
        return
    try:
        if not os.path.isdir(root):
            os.makedirs(root)
        stamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        with io.open(os.path.join(root, 'ET_TRACE.txt'), 'a', encoding='utf-8') as out:
            out.write(text(stamp + ' ' + message + '\n'))
            out.flush()
    except Exception:
        pass
