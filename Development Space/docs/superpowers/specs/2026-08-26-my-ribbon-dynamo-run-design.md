# My Ribbon - why a Dynamo button did nothing, and what now runs it

*2026-08-26*

## The report

A Dynamo button placed by My Ribbon appeared on the ribbon, was enabled, and
did nothing at all when clicked. No dialog, no pyRevit output window, no error.

## What EasyBIM does, and what it does not do

EasyBIM never runs a graph. A Dynamo source becomes a real pyRevit bundle -
`EasyBIM_MyRibbon.extension\My Ribbon Library.tab\Dynamo.panel\<Title>.pushbutton`
holding `script.dyn` and a `bundle.yaml` - and pyRevit's own `DynamoBIMEngine`
runs it. So everything between the click and Dynamo belongs to pyRevit, and the
investigation had to go through pyRevit's source rather than ours.

## Ruled out: the bundle.yaml we write

The 2026-08-16 spec recorded one assumption it could not test - that pyRevit
compares `automate` against the *string* `'true'`, so the unquoted
`automate: true` we write would satisfy it. **That assumption is correct**, and
it is worth writing down so nobody re-opens it:

- `pyrevit/coreutils/yaml.py::_convert_yamldotnet_to_dict` returns
  `YamlScalarNode.Value` for every scalar, so pyRevit's bundle metadata is
  strings all the way down - there is no YAML boolean to trip over.
- `pyrevit/extensions/genericcomps.py::_read_bundle_metadata` therefore reads
  `automate: true` as `'true' == 'true'` and sets `requires_mainthread_engine`,
  and reads `dynamo_path` as the path string.
- `pyrevit/runtime/dynamotypemaker.py` serialises those into the engine JSON,
  and `DynamoBIMEngine.Execute` turns them into journal data: `dynPath` (our
  original graph), `dynAutomation="True"`, `dynPathExecute="True"`,
  `dynModelShutDown` from `clean`, `dynShowUI` from pyRevit's debug mode.

The bundle we write is a correct pyRevit Dynamo bundle. The problem is in the
graph.

## The cause: a graph saved in Manual run mode is opened and never run

pyRevit launches a graph through Dynamo's journal interface, and a graph whose
saved run mode is Manual is opened but not executed - pyRevit's own forum
carries the report and the confirmation ("Dynamo scripts set to manual run
won't be executed by pyRevit"; the fix users confirmed was switching the graph
to Automatic). Because `dynShowUI` is false on a plain click, there is no
window and no error to see it happen: the click is silent.

Dynamo also re-saves a graph in a manual state after a crash, so a graph can
arrive in this state without its author ever choosing it.

The file says which mode it is in, which is what makes this fixable:

- 2.x JSON: `View.Dynamo.RunType` - `"Automatic"`, `"Manual"` or `"Periodic"`
- 1.x XML: the `RunType` attribute of `<Workspace>`

## What we now do

`dynamo_facts_from_text` reads `run_type` alongside the Python engines it
already read, and `dynamo_needs_forced_run` is true for any mode that is not
Automatic. For such a graph, and only such a graph:

- `refresh_dynamo_copy` writes `script.dyn` through `force_automatic_run`,
  which substitutes that one value textually. The copy is byte-for-byte the
  user's graph everywhere else, and **the user's own file is never written**.
  A file that does not carry exactly one `RunType` is copied unpatched rather
  than rewritten by guesswork.
- `desired_dynamo_yaml` leaves `dynamo_path` out, so pyRevit opens our patched
  copy instead of the original - otherwise the fix would sit in a file pyRevit
  never opens.
- The trade-off is stated in the picker, the source row and the button tooltip:
  a Manual graph runs from a copy, so edits to it count from the next Apply
  rather than the next click. An Automatic graph keeps the original behaviour -
  run where it lives, edits count immediately.
- If the copy still reads as Manual after Apply, `sync_dynamo_bundles` reports
  it by name rather than leaving a dead button on the ribbon.

## The Python engine

The graph's Python nodes carry their own engine (`IronPython2`, `CPython3`,
`PythonNet3`), and Dynamo - not pyRevit - honours it; pyRevit exposes no knob
for it. What pyRevit does expose is `clean`, its `dynModelShutDown`, which
tears down a Dynamo model left from an earlier run before opening the graph.
That matters to exactly one of the engines:

- **CPython3 / PythonNet3** - the evaluator that fails beside pyRevit's own
  engine assemblies ("attempt to read or write protected memory",
  "PythonEvaluator.Evaluate operation failed"; pyRevit issue #2400 and
  Autodesk's own notes). These bundles now get `clean: true`, so the graph
  meets a fresh Dynamo model. pyRevit's source notes the shutdown costs about
  3x on start-up, which is why it is not applied to everything.
- **IronPython2** - no `clean`, and a tooltip line instead: Dynamo 2.7 and
  newer ship without IronPython, so those nodes need Dynamo's
  `DynamoIronPython2.7` package or they fail silently too.

`dynamo_engine` reduces the per-node list to one verdict (`"mixed"` when the
nodes disagree, `""` when there is no Python node); a Python node with no
`Engine` key is read as `IronPython2`, which is Dynamo's own fallback.

## Two causes we cannot fix from here

Recorded so they are not re-investigated:

1. **pyRevit 6.x with Revit 2025/2027.** `.dyn` buttons can fail before
   reaching the execution layer at all - no log entry on click. The workaround
   reported and confirmed on the pyRevit forum is pyRevit's own setting
   *launcher = legacy*. Nothing in a bundle can influence this.
2. **Dynamo without IronPython.** Covered above: it is a missing Dynamo
   package, installed in Dynamo, not something a pyRevit bundle can supply.

## Pinned by

- `test_my_ribbon_state.DynamoHelperTests` - `run_type` and `engine` off both
  graph formats, `force_automatic_run` changing that one value and nothing
  else, refusing a file with no single answer, and the `clean` flag of
  `render_dynamo_bundle_yaml`.
- `test_my_ribbon_host.DynamoBundleTests` - a Manual graph loses `dynamo_path`
  and gains a patched copy while the original is untouched, a patched copy is
  current until the original moves on, `clean` only for CPython, and an
  unpatchable graph is reported.
- `test_my_ribbon_command_names.MyRibbonContractTests` - the patch is written
  to the copy and never to the user's file, and is a substitution rather than a
  re-serialisation.

## Still to verify in Revit

Nothing here has run in Revit. In order of risk:

1. A graph saved in Manual run mode: add it, Apply, reload, click - it runs.
2. An Automatic graph still runs from its own location and picks up an edit
   with no Apply.
3. A CPython3 graph runs, and its `bundle.yaml` carries `clean: true`.
4. Editing a Manual graph and pressing Apply refreshes the patched copy.
5. A graph moved away still runs its last copy, and **Locate graph...** fixes it.
