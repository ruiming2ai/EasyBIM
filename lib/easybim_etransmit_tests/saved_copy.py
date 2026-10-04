# -*- coding: utf-8 -*-
"""H only: task-document ownership is not inferred from its pre-save title.

Production and A-G keep their existing backends. All H output is experimental.
"""
from __future__ import unicode_literals
import ntpath
import os

from .base import files as f
from .base.revit import Backend, dispose
from .probes import ProbeBackend


def same_document(left, right):
    """Use API object identity/equality, never a display name or a filename."""
    if left is right:
        return True
    equals = getattr(left, 'Equals', None)
    return bool(equals(right)) if equals is not None else bool(left == right)


class SavedCopyBackend(ProbeBackend):
    """Correct only the H probe's opening and cleanup, retaining path guards."""

    def absolute_guard(self, path):
        if not f.absolute(f.text(path)):
            raise ValueError('H requires a fully qualified local file path: ' + f.text(path))
        self.guard(path)

    def _owned_open(self, path, opener, detach):
        self.absolute_guard(path)
        # Failure to enumerate documents is not permission to assume ownership.
        existing = list(self.app.Documents)
        self.phase('open_copy', path=path, detach_requested=bool(detach))
        doc = opener()
        if doc is None:
            raise RuntimeError('Revit did not return a document.')
        if any(same_document(doc, old) for old in existing):
            raise RuntimeError('An already-open document was returned; no edits or Close attempted.')
        if bool(doc.IsLinked):
            raise RuntimeError('A linked document was returned; no edits or Close attempted.')

        # Register BEFORE inspecting PathName or writing diagnostics. A rejection
        # or diagnostic I/O error must not leak this newly returned task document.
        self.owned_documents.append(doc)
        try:
            self.evidence.track(doc)
            state = self.evidence.snapshot(doc, 'immediately after task open')
            self.dirty_seen = self.dirty_seen or bool(doc.IsModified)
            actual = f.text(doc.PathName or '')
            if f.absolute(actual):
                self.guard(actual)
            elif detach and (not actual or (
                    actual == ntpath.basename(actual) and
                    ':' not in actual and actual.lower().endswith('.rvt'))):
                self.evidence.write('temporary_document_name', value=actual,
                    trusted_input=path, state=state,
                    meaning='Pre-save API name only; never used as a read/write target.')
            else:
                raise ValueError('Unexpected pre-save document pathname: ' + actual)
            if not detach:
                if bool(doc.IsDetached) or f.canonical(actual) != f.canonical(path):
                    raise RuntimeError('Normal reopen did not return the saved candidate at its exact path.')
            if bool(doc.IsReadOnly):
                raise RuntimeError('Task document is read-only; no repair attempted.')
            return doc
        except Exception:
            # A Close failure retains ownership for the runner's final cleanup.
            self.close_document(doc)
            raise

    def open_copy(self, path, discard=False, close_worksets=False, detach=True):
        return self._owned_open(path, lambda: Backend.open_copy(
            self, path, discard=discard, close_worksets=close_worksets,
            detach=detach), detach)

    def close_document(self, doc):
        if not any(same_document(doc, old) for old in self.owned_documents):
            raise RuntimeError('Refusing to close a document not owned by H.')
        # Do not replace the diagnostic failure stage with a cleanup stage.
        if not doc.Close(False):
            raise RuntimeError('Task document could not close; candidate remains unaccepted.')
        self.owned_documents = [old for old in self.owned_documents
                                if not same_document(doc, old)]
        self.evidence.documents = [old for old in self.evidence.documents
                                   if not same_document(doc, old)]
        self.evidence.write('task_document_closed', save_pending_changes=False)

    def reference_with_status(self, doc, row):
        value = self.actual_reference(doc, row)
        ref = None
        try:
            ref = self.DB.ExternalFileUtils.GetExternalFileReference(
                doc, self.ident(row['element_id']))
            value['loaded_state'] = f.text(ref.GetLinkedFileStatus())
        except Exception as exc:
            value['load_state_error'] = f.text(exc)
        finally:
            dispose(ref)
        return value

    def check_saved_host(self, target, workshared):
        self.absolute_guard(target)
        if not os.path.isfile(target):
            raise RuntimeError('Save did not produce a candidate file.')
        self.phase('check_saved_host', target=target)
        self.verify_independent_package(target, [], {'repath': False},
                                        expected_workshared=workshared)

    def reload_selected_cad(self, doc, row, original_identity):
        """One supported DWG reload. SaveAs may rebase its OLD relative path.

        The original path was checked BEFORE SaveAs. After normalizing, match
        the same element's UniqueId and API type, not a rebased old path string.
        """
        current = self.actual_reference(doc, row)
        if (not current.get('present') or
                current.get('unique_id') != original_identity.get('unique_id') or
                current.get('type') != original_identity.get('type')):
            raise RuntimeError('Selected CAD identity changed during SaveAs; no substitute used.')
        self.absolute_guard(row['target'])
        if not os.path.isfile(row['target']):
            raise ValueError('The collected DWG is missing.')
        element = doc.GetElement(self.ident(row['element_id']))
        is_link = getattr(element, 'IsLink', None)
        if callable(is_link):
            is_link = is_link()
        if is_link is False:
            raise ValueError('Selected CAD is an import, not a link.')
        self.phase('CAD_LoadFrom', element_id=row['element_id'], target=row['target'])
        result = None
        try:
            result = element.LoadFrom(row['target'])
            from .base.revit import eid
            row['api_result'] = dict(load_result=f.text(result.LoadResult),
                                     returned_element_id=eid(result.ElementId))
            self.evidence.write('cad_load_result', result=row['api_result'])
            if row['api_result']['returned_element_id'] != f.text(row['element_id']):
                raise RuntimeError('CAD_TARGET_COLLISION: Revit returned a different CAD type.')
            if row['api_result']['load_result'] not in ('LinkLoaded', 'LinkAlreadyLoaded'):
                raise RuntimeError('CAD reload failed: ' + row['api_result']['load_result'])
            row['after_api'] = self.reference_with_status(doc, row)
        finally:
            dispose(result)


def run_saved_copy(backend, stage, target, rows, options):
    """Save a normal task copy, repair ONE DWG, save once and verify on reopen.

    No native TransmissionData edits, no source Save/Sync, and no promotion.
    Unknown post-save edits are discarded on close and remain a review flag.
    Other links can load via their installed providers; they are NOT repathed.
    """
    from . import scenarios
    ids = options.get('selected_ids', [])
    if len(ids) != 1:
        raise ValueError('H requires exactly one selected linked DWG.')
    cad = [r for r in rows if r.get('kind') == 'CADLink' and r.get('target') and
           r['target'].lower().endswith('.dwg')]
    selected = scenarios.select_rows(cad, ids)
    row = selected[0]
    if row.get('loaded') is False or row.get('original_loaded') is False:
        raise ValueError('H tests a loaded CAD link; it will not change an unloaded link intent.')
    backend.absolute_guard(stage)
    backend.absolute_guard(target)
    backend.absolute_guard(row['target'])
    if os.path.exists(target):
        raise ValueError('H never overwrites an existing candidate.')
    info = backend.basic(stage)
    if f.text(info.get('version')) != f.text(backend.app.VersionNumber):
        raise ValueError('H requires the saved model Revit version; no automatic upgrade.')
    workshared = bool(info.get('workshared'))
    original_hash = f.digest(stage)
    cad_hash = f.digest(row['target'])
    doc = None
    try:
        # Use the original open-workset lifecycle, not A-F's synthetic closed-
        # workset setup. This makes the selected type and its instances available.
        doc = backend.open_copy(stage)
        backend.phase('check_original_CAD_identity')
        original = backend.verify_identity(doc, row)
        if not original.get('unique_id'):
            raise RuntimeError('Selected CAD has no readable UniqueId.')
        row['before_saveas'] = original
        state = backend.observed_save_as(doc, target,
                                         backend._package_is_transmitted(stage), strict=False)
        row['first_save_state'] = state
        backend.close_document(doc)
        doc = None
        backend.check_saved_host(target, workshared)

        # No pending callback changes are resaved merely to clear IsModified.
        # Reopen the bytes actually saved, normally, before performing repairs.
        doc = backend.open_copy(target, detach=False)
        backend.dirty_seen = backend.dirty_seen or bool(doc.IsModified)
        backend.reload_selected_cad(doc, row, original)
        backend.phase('save_CAD_repair')
        backend.evidence.snapshot(doc, 'before CAD Save')
        doc.Save()
        backend.dirty_seen = backend.dirty_seen or bool(doc.IsModified)
        backend.evidence.snapshot(doc, 'after CAD Save')
        backend.close_document(doc)
        doc = None
        backend.check_saved_host(target, workshared)

        doc = backend.open_copy(target, detach=False)
        backend.phase('verify_CAD_after_normal_reopen')
        backend.dirty_seen = backend.dirty_seen or bool(doc.IsModified)
        actual = backend.reference_with_status(doc, row)
        row['after_reopen'] = actual
        matched = bool(actual.get('present') and
                       actual.get('unique_id') == original['unique_id'] and
                       scenarios.key_path(actual.get('absolute')) == scenarios.key_path(row['target']))
        loaded = actual.get('loaded_state') == 'Loaded'
        relative = actual.get('path_type') == 'Relative'
        verified = matched and loaded
        row.update(path_matches_after_reopen=matched, load_verified=loaded,
                   path_is_relative=relative, saved_reference_verified=verified)
        backend.evidence.write('CAD_saved_reference_check', expected=row['target'],
                               actual=actual, path_matches=matched, loaded=loaded)
        if not verified:
            status = 'FAILED_SAVED_CAD_VERIFICATION'
            message = 'The reopened CAD path or loaded state did not match; inspect the recorded actual reference.'
        elif backend.dirty_seen or not relative:
            status = 'CAD_REPATH_VERIFIED_REVIEW_REQUIRED'
            message = ('Selected CAD repath and normal opening verified. Review: ' +
                       ('post-save/open modified state; ' if backend.dirty_seen else '') +
                       ('stored path is absolute; portability not proven.' if not relative else
                        'no production-safety conclusion from this experiment.'))
        else:
            status = 'CAD_REPATH_VERIFIED_TEST_ONLY'
            message = 'Selected CAD path and loaded state verified after normal reopening. Other references were not repaired.'
        return dict(trial='saved_copy_cad', status=status, message=message,
                    rows=selected, normal_open_verified=True,
                    saved_reference_verified=verified, path_is_relative=relative,
                    dirty_seen=backend.dirty_seen, candidate_path=target,
                    scope='ONE_SELECTED_DWG_ONLY', input_stage_sha256=original_hash)
    finally:
        if doc is not None and any(same_document(doc, old) for old in backend.owned_documents):
            backend.close_document(doc)
        if f.digest(stage) != original_hash or f.digest(row['target']) != cad_hash:
            raise RuntimeError('H input bytes changed unexpectedly; no output accepted.')
