[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$pyinstaller = Join-Path $repoRoot ".venv\Scripts\pyinstaller.exe"
$spec = Join-Path $PSScriptRoot "ignition_tag_editor.spec"
$exe = Join-Path $repoRoot "dist\IgnitionTagEditor\IgnitionTagEditor.exe"

if (-not (Test-Path -LiteralPath $pyinstaller)) {
    throw "PyInstaller ni namescen. Pozeni: .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt"
}

Push-Location $repoRoot
try {
    & $pyinstaller --noconfirm --clean $spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build ni uspel (exit $LASTEXITCODE)."
    }
    if (-not (Test-Path -LiteralPath $exe)) {
        throw "Pricakovana izvrsljiva datoteka ne obstaja: $exe"
    }
    $process = Start-Process -FilePath $exe -ArgumentList "--smoke-test" -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Zapakirana aplikacija ni prestala smoke testa (exit $($process.ExitCode))."
    }
    Write-Output "BUILD_VERIFIED: $exe"
}
finally {
    Pop-Location
}
