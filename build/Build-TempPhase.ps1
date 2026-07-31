[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("2025", "2026")]
    [string] $RevitVersion,

    [string] $ExtensionRoot,

    [string] $ArtifactPath,

    [switch] $NoStage
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ExtensionRoot)) {
    $ExtensionRoot = $repoRoot
}

try {
    $ExtensionRoot = (Resolve-Path -LiteralPath $ExtensionRoot -ErrorAction Stop).ProviderPath
}
catch {
    throw "The EasyBIM extension root does not exist: $ExtensionRoot"
}

$project = Join-Path $repoRoot ("src\TempPhase\TempPhase.Revit{0}\TempPhase.Revit{0}.csproj" -f $RevitVersion)
$configuration = "Release"
$bundleDir = Join-Path $ExtensionRoot "EasyBIM.tab\Misc Tools.panel\Temp Phase.pushbutton"
$stageDir = Join-Path $bundleDir "bin"

function Get-AssemblyIdentity([string] $assemblyPath) {
    try {
        return [System.Reflection.AssemblyName]::GetAssemblyName($assemblyPath)
    }
    catch {
        throw "Could not read .NET assembly metadata from '$assemblyPath': $($_.Exception.Message)"
    }
}

function Assert-DeploymentBundle([string] $targetBundleDir, [string] $targetStageDir, [string] $targetVersion, [switch] $RequireController) {
    $bundleFile = Join-Path $targetBundleDir "bundle.yaml"
    $scriptFile = Join-Path $targetBundleDir "script.cs"
    $controllerFile = Join-Path $targetStageDir "TempPhaseController.dll"
    $expectedAssemblyName = "TempPhaseController.Revit$targetVersion"

    if (-not (Test-Path -LiteralPath $bundleFile -PathType Leaf)) {
        throw "EasyBIM Temp Phase bundle.yaml was not found: $bundleFile"
    }

    if (-not (Test-Path -LiteralPath $scriptFile -PathType Leaf)) {
        throw "EasyBIM Temp Phase script.cs was not found: $scriptFile"
    }

    $apiAssemblies = @(Get-ChildItem -LiteralPath $targetStageDir -Filter "RevitAPI*.dll" -File -ErrorAction SilentlyContinue)
    if ($apiAssemblies.Count -gt 0) {
        $names = ($apiAssemblies | ForEach-Object { $_.Name }) -join ", "
        throw "Revit API assemblies must not be copied beside TempPhaseController.dll: $names"
    }

    if ($RequireController -and -not (Test-Path -LiteralPath $controllerFile -PathType Leaf)) {
        throw "TempPhaseController.dll was not found in the live extension bundle: $controllerFile"
    }

    if (Test-Path -LiteralPath $controllerFile -PathType Leaf) {
        $identity = Get-AssemblyIdentity $controllerFile
        if ($RequireController -and $identity.Name -ne $expectedAssemblyName) {
            throw "The staged controller assembly is '$($identity.Name)', but Revit $targetVersion requires '$expectedAssemblyName'. Path: $controllerFile"
        }
        if (-not $RequireController -and $identity.Name -ne $expectedAssemblyName) {
            Write-Host ("Existing staged controller is '{0}'; it will be replaced with '{1}'." -f $identity.Name, $expectedAssemblyName)
        }
    }
}

Write-Host ("Source repository: {0}" -f $repoRoot)
Write-Host ("Deployment root: {0}" -f $ExtensionRoot)

if ([string]::IsNullOrWhiteSpace($ArtifactPath)) {
    if (-not (Test-Path -LiteralPath $project -PathType Leaf)) {
        throw "Temp Phase project not found: $project"
    }

    $dotnetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
    $dotnetPath = if ($dotnetCommand) {
        $dotnetCommand.Source
    } else {
        Join-Path ${env:ProgramFiles} "dotnet\dotnet.exe"
    }

    if (-not (Test-Path -LiteralPath $dotnetPath -PathType Leaf)) {
        throw "dotnet SDK was not found. Install the .NET 8 SDK before building."
    }

    Write-Host ("Building Temp Phase controller for Revit {0}" -f $RevitVersion)
    $buildOutput = & $dotnetPath build $project --configuration $configuration --nologo 2>&1
    $buildExitCode = $LASTEXITCODE
    $buildOutput | ForEach-Object { Write-Host $_ }
    if ($buildExitCode -ne 0) {
        throw "Temp Phase Revit $RevitVersion build failed."
    }

    $artifact = Join-Path $repoRoot ("src\TempPhase\TempPhase.Revit{0}\bin\{1}\net8.0-windows\TempPhaseController.Revit{0}.dll" -f $RevitVersion, $configuration)
}
else {
    try {
        $artifact = (Resolve-Path -LiteralPath $ArtifactPath -ErrorAction Stop).ProviderPath
    }
    catch {
        throw "The prebuilt Temp Phase artifact does not exist: $ArtifactPath"
    }
    Write-Host ("Using prebuilt Temp Phase artifact: {0}" -f $artifact)
}

if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
    throw "Temp Phase artifact was not found: $artifact"
}

$artifactIdentity = Get-AssemblyIdentity $artifact
$expectedArtifactName = "TempPhaseController.Revit$RevitVersion"
if ($artifactIdentity.Name -ne $expectedArtifactName) {
    throw "The build produced assembly '$($artifactIdentity.Name)', but expected '$expectedArtifactName'. Path: $artifact"
}

if (-not $NoStage) {
    # Validate the live clone before changing it. This catches the common case
    # where pyRevit is loading a different extension copy than this checkout.
    Assert-DeploymentBundle $bundleDir $stageDir $RevitVersion
    New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
    $staged = Join-Path $stageDir "TempPhaseController.dll"
    Copy-Item -LiteralPath $artifact -Destination $staged -Force
    Assert-DeploymentBundle $bundleDir $stageDir $RevitVersion -RequireController
    Write-Host ("Staged Revit {0} controller at {1}" -f $RevitVersion, $staged)
}
else {
    Write-Host "NoStage specified; the live extension was not changed."
}

Write-Host ("Built {0}" -f $artifact)
