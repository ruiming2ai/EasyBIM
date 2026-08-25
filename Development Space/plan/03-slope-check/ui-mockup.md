# Slope Check — UI Mockup & Operation Diagrams

> Visual companion to handoff.md, which is authoritative. Mockups are ASCII wireframes; diagrams are Mermaid (rendered by GitHub).

## Window: Setup

Gravity system types and the minimum-slope-by-diameter table sit side by side; nothing is read from the model until Check is pressed:

```
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│Slope Check                                                                              _  □  X│
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Slope Check                                                                                    │
│ Document: Riverside Medical Center - Plumbing.rvt                                              │
│                                                                                                │
│  ┌────────────────────────────────────────────────┐  ┌────────────────────────────────────────┐│
│  │ Gravity systems                                │  │ Minimum slope      [ Save ]  [ Load ]  ││
│  ├────────────────────────────────────────────────┤  ├────────────────────────────────────────┤│
│  │ Search:  [                                    ]│  │ DIAMETER UP TO    MIN SLOPE            ││
│  │ 3 of 11 system types selected.                 │  │ < 3 in            1/4 in/ft            ││
│  │ [ Select All ]   [ Select None ]               │  │ 3 in to 6 in      1/8 in/ft            ││
│  │  ┌──────────────────────────────────────────┐  │  │ * > 6 in          1/16 in/ft           ││
│  │  │ [x] Sanitary                             │  │  │                                        ││
│  │  │ [x] Storm                                │  │  │ * edited since load — red until Save   ││
│  │  │ [x] Condensate                           │  │  │                                        ││
│  │  │ [ ] Sanitary Vent                        │  │  │                                        ││
│  │  │ [ ] Domestic Cold Water                  │  │  │                                        ││
│  │  └──────────────────────────────────────────┘  │  │                                        ││
│  │ + 6 more — scroll for full list                │  │                                        ││
│  └────────────────────────────────────────────────┘  └────────────────────────────────────────┘│
│                                                                                                │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Pumped mains left unchecked are excluded, and the report will say so.   [  Check  ]  [ Cancel ]│
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- Rows in the Minimum slope grid render red as soon as they are edited; the leading * above marks that state since the wireframe cannot show colour. They turn black again only after Save.
- Sanitary, Storm, and Condensate are pre-checked as the built-in gravity classifications; any other system type must be ticked on by hand, and anything left unticked is footer-listed as excluded once the walk runs.
- Cancel here reads nothing beyond the two setup lists — the connector graph is never walked.

## Window: Results

A modeless, read-only table grouped by system, with one expander per finding kind:

```
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│Slope Check — Results                                                                    _  □  X│
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Slope Check — Results                                                                          │
│ Document: Riverside Medical Center - Plumbing.rvt                                              │
│                                                                                                │
│  ▾ Reversals  (3)                                                                              │
│    SYSTEM  ELEMENT IDS      LEVEL MEASURED      REQUIRED                                       │
│    SAN 1   412200, 412204   L1    falls uphill  toward outfall   [Show]                        │
│                                                                                                │
│  ▾ Below minimum  (17)                                                                         │
│    SYSTEM  ELEMENT IDS      LEVEL MEASURED      REQUIRED                                       │
│    SAN 2   412310           L1    1/16 in/ft    1/4 in/ft   [Show]                             │
│                                                                                                │
│  ▸ Flat  (5)                                                                                   │
│  ▸ Invert steps  (2)                                                                           │
│                                                                                                │
│    SAN 3 — outfall ambiguous                                                   [ Pick outfall ]│
│                                                                                                │
│  ▾ Named skips                                                                                 │
│    SAN 2 — traversal stopped at open end at id 400123 — downstream not judged                  │
│    Fitting id 512907 — no readable connectors                                                  │
│                                                                                                │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Walked 41 runs; 2 stopped at open ends (listed). Nothing was changed.                          │
│                                                            [ Refresh ]  [ Export ]  [  Close  ]│
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- Reversals, Below minimum, Flat, and Invert steps are each their own expander with an independent count; Flat and Invert steps are shown collapsed here only to save space.
- "SAN 3 — outfall ambiguous" carries its own Pick outfall button; clicking it prompts PickObject through the bridge and re-judges only that system in place.
- The session's outfall picks and every expander's open or closed state survive Refresh; only the underlying findings change.
- Named skips are listed by reason and are never counted as findings — a traversal that stops at an open end names Open Ends as the tool that resolves it.

## User operation flow

```mermaid
flowchart TD
    A[Ribbon Misc Tools, MEP Checks, Slope Check] --> B[Setup window opens, gravity systems pre checked, default minimums loaded]
    B --> C[Adjust system types and the minimum slope table, edited rows show red until saved]
    C --> D{Click Check or Cancel}
    D -->|Cancel| Z[Setup closes, nothing read beyond the setup lists]
    D -->|Check| E[Results window opens, connector graph walk runs, read only, nothing written]
    E --> F[Status ticks per system]
    F --> G[Findings fill expanders, reversals first, then below minimum, flat, invert steps]
    F --> H[Named skips expander, stopped at open end, no readable connectors, flex pipe not judged]
    G --> I{System marked outfall ambiguous}
    I -->|Yes| J[Pick outfall button, PickObject through the bridge]
    J --> K[User picks the point of connection]
    K --> L[That system re judges in place]
    L --> M{Next action}
    I -->|No| M
    H --> M
    M -->|Show on a finding| N[Revit selects and zooms to the elements]
    N --> O[User fixes the routing, drags the pipe]
    O --> M
    M -->|Refresh| E
    M -->|Export| P[Write findings and named skips to xlsx]
    P --> M
    M -->|Close or Esc| Q[Results window closes, nothing was ever written]
```
