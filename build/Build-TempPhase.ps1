[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("2025", "2026")]
    [string] $RevitVersion,

    [switch] $NoStage
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $repoRoot ("src\TempPhase\TempPhase.Revit{0}\TempPhase.Revit{0}.csproj" -f $RevitVersion)
$configuration = "Release"

if (-not (Test-Path $project)) {
    throw "Temp Phase project not found: $project"
}

$dotnetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
$dotnetPath = if ($dotnetCommand) {
    $dotnetCommand.Source
} else {
    Join-Path ${env:ProgramFiles} "dotnet\dotnet.exe"
}

if (-not (Test-Path $dotnetPath)) {
    throw "dotnet SDK was not found. Install the .NET 8 SDK before building."
}

$buildOutput = & $dotnetPath build $project --configuration $configuration --nologo 2>&1
$buildExitCode = $LASTEXITCODE
$buildOutput | ForEach-Object { Write-Host $_ }
if ($buildExitCode -ne 0) {
    throw "Temp Phase Revit $RevitVersion build failed."
}

$artifact = Join-Path $repoRoot ("src\TempPhase\TempPhase.Revit{0}\bin\{1}\net8.0-windows\TempPhaseController.Revit{0}.dll" -f $RevitVersion, $configuration)
if (-not (Test-Path $artifact)) {
    throw "Build completed but expected artifact was not found: $artifact"
}

if (-not $NoStage) {
    $stageDir = Join-Path $repoRoot "EasyBIM.tab\Misc Tools.panel\Temp Phase.pushbutton\bin"
    New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
    $staged = Join-Path $stageDir "TempPhaseController.dll"
    Copy-Item -Path $artifact -Destination $staged -Force
    Write-Host ("Staged Revit {0} controller at {1}" -f $RevitVersion, $staged)
}

Write-Host ("Built {0}" -f $artifact)
