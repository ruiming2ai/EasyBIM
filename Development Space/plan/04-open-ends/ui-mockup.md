# Open Ends — UI Mockup & Operation Diagrams

> Visual companion to handoff.md, which is authoritative. Mockups are ASCII wireframes; diagrams are Mermaid (rendered by GitHub).

## Window: Open Ends Pane

A dockable pane, right by default, scanning the model as soon as it opens:

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│Open Ends                                                                          _  □  X│
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ Open Ends                                                                                │
│ Document: Harbor Point Data Center - MEP.rvt                                             │
│                                                                                          │
│ Search:  [ 12                                        ]                                   │
│ [x] Duct  [x] Pipe  [x] Cable Tray  [x] Conduit   [ ] Include electrical connectors      │
│ Touching tolerance:  [ 5 mm ]                                                            │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│  ▾ Touching, not connected  (7)                                                          │
│      Duct  SA-1   AHU-3 discharge / VAV-12 inlet                          [Show] [Ignore]│
│      Pipe  SAN 2  Fitting 512907 / Fitting 512910                         [Show] [Ignore]│
│                                                                                          │
│  ▾ Duct                                                                                  │
│    ▾ Supply Air                                                                          │
│      ▾ SA-1                                                                              │
│          VAV-08 inlet — spare tap                                         [Show] [Ignore]│
│                                                                                          │
│  ▾ Pipe                                                                                  │
│    ▾ Sanitary                                                                            │
│      ▾ SAN 3                                                                             │
│          Cleanout CO-14                                                   [Show] [Ignore]│
│          could not read (2) — ids 550012, 550014                                         │
│                                                                                          │
│  ▸ Cable Tray  (12)                                                                      │
│  ▸ Conduit  (31)                                                                         │
│                                                                                          │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ Scanned 8,412 elements in 1.9 s — 63 open ends, 7 touching pairs. Electrical excluded.   │
│                                                                   [ Refresh ]  [ Export ]│
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

- Docks to the right by default; if the pane cannot register this session, a modeless fallback window opens in its place with a restart-once note in its subtitle — not shown here.
- Ignore lasts only for the current Revit session; its tooltip says so, and the row simply greys out of the counts rather than disappearing.
- Electrical physical connectors are excluded by default (the "Include electrical connectors" chip above is unticked), which is why the footer names them excluded rather than silently omitting them.
- There is no Cancel or Close button in the footer — closing the pane itself is the whole cancel path, since nothing here is ever written.

## User operation flow

```mermaid
flowchart TD
    A[Ribbon Misc Tools, Coordination, Open Ends] --> B[Pane docks right, or fallback window opens with restart once note]
    B --> C[First scan runs automatically, status ticks]
    C --> D[Tree fills, touching not connected group first and pre expanded]
    D --> E[Then one branch per domain, system type, system, element]
    D --> F[Could not read bucket lists elements whose ConnectorManager threw]
    D --> G[Footer notes any unticked filter chip as excluded]
    E --> H{Choose an action on a row}
    H -->|Show| I[Revit selects and zooms to the open connectors]
    I --> J[User closes the connection or fixes the routing]
    J --> K{Next action}
    H -->|Ignore| L[Row greys out of the counts, session only, tooltip says so]
    L --> K
    F --> K
    G --> K
    K -->|Search or filter chips| E
    K -->|Refresh| C
    K -->|Export| M[Write the flat classified list to xlsx]
    M --> K
    K -->|Close the pane| N[Closed, nothing was ever written]
    N --> O[Next open re scans fresh, ignores survive only until Revit closes]
```
