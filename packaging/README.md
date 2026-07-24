# Windows packaging

From the repository root in Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\packaging\build.ps1
```

The verified onedir bundle is written to:

```text
dist\IgnitionTagEditor\IgnitionTagEditor.exe
```

The build script always performs a clean PyInstaller build and then launches the
packaged executable with its noninteractive smoke-test flag. `build\` and `dist\` are
generated, ignored directories and are not committed.

Production export remains offline. After importing the generated JSON into Ignition,
re-export the same scope from Ignition and select that JSON on the application's
`Izvoz` page. The editor compares it with the planned simulated scope; it does not
connect to or modify a Gateway.
