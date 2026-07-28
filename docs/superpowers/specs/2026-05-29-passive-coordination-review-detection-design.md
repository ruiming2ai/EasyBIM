# Passive Coordination Review Detection Design

## Summary

Replace the native Coordination Review dialog automation goal with passive detection. EasyBIM will watch Revit's normal warning/failure flow and record linked models that Revit reports as needing Coordination Review. The Coordination Review window will still open automatically after Start Message, but it will only show problem links captured during file open or link load/reload.

## Goals

- Detect linked models that need Coordination Review without forcing link reloads.
- Avoid opening or driving Revit's native Coordination Review dialog.
- Show only linked models that need Coordination Review.
- Show a short `Detection Error` message when no passive warning was captured.
- Keep detailed diagnostic information internal for logs/tests, not in the user-facing window.

## Non-Goals

- Do not list individual Coordination Review issue details.
- Do not claim links are clear when no warning was captured.
- Do not require users to select links manually.
- Do not force reload all links during startup.

## User Behavior

EasyBIM will continue to open the Coordination Review window automatically after Start Message.

The window content will be:

- Problem links only, when passive detection captures one or more Coordination Review warnings.
- `Detection Error`, when no Coordination Review warning was captured for the opened document/session.

## Detection Flow

At extension startup, EasyBIM registers a lightweight listener for Revit warning/failure events. When Revit naturally raises `BuiltInFailures.LinkFailures.LinkInstanceNeedsReconcile`, the listener records the affected link for the active document.

The Start Message workflow reads the recorded session state and builds the Coordination Review window model from that state. This replaces the current native HTML-report automation path for the default startup/manual report flow.

## Data Model

Captured records should contain only minimal session data:

- Document identity: normalized path when available, otherwise title.
- Link identity: link instance/type id and link name when Revit exposes them.
- Fallback text: warning text when the link cannot be mapped.
- Timestamp.
- Status: `needs_coordination_review`.

If the warning is detected but cannot be mapped to a specific link, EasyBIM should show one generic problem row:

`Linked model needs Coordination Review`

## Error Handling

If passive detection records no Coordination Review warning for the target document, the window should show only:

`Detection Error`

This is intentional. Passive detection cannot prove that links are clear, so the UI must not display a clear/success state when no warning was captured.

Unrelated Revit warnings must be ignored.

## Testing

Unit tests should cover pure parser/state behavior:

- Detects the Coordination Review warning by built-in failure id.
- Extracts link ids/names when available.
- Falls back to a generic problem row when mapping fails.
- Returns the `Detection Error` model when no captured warnings exist.
- Ignores unrelated Revit warnings.

Manual Revit validation should cover:

- Open the Belmont file that triggers native Coordination Review warnings.
- Confirm EasyBIM opens the Coordination Review window automatically after Start Message.
- Confirm only captured problem links are listed.
- Confirm no native Coordination Review dialog opens.
- Confirm a session with no captured warning shows `Detection Error`.
