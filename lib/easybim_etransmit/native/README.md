# EasyBIM.FileIO

The bundled `EasyBIM.FileIO.dll` (7,680 bytes) is built from the readable source in
`Development Space/native/EasyBIM.FileIO/`. It is an in-process .NET Standard 2.0
helper, not a Revit add-in or a separate program. Users do not install an SDK or
compile it. Update the whole extension so this file is included.

SHA-256:
`bf358101dbd99ff9c2a42bdcef108b320ad9b0fbb4460d86394b730bfeee9968`

IronPython calls this helper for Unicode Win32 file operations because raw ctypes
error propagation differs from CPython. P/Invoke records errors inside the managed
boundary. It performs only local file operations; no networking, registry edits,
Revit transactions, geometry changes or source-model saves are implemented here.

CI recompiles this source, compares its hash with the bundled DLL, and runs actual
long-path copying under Windows IronPython. The workflow has read-only repository
permissions and does not publish or modify repository files.
