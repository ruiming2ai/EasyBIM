# Developer-only fallback build.  The normal EasyBIM pyRevit workflow is
# Python-only and does not load or stage these controller assemblies.
[CmdletBinding()]
param(
    [ValidateSet("2025", "2026", "All")]
    [string] $RevitVersion = "All",

    [switch] $VerifyOnly
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$configuration = "Release"
$sourceBundleDir = Join-Path $repoRoot "EasyBIM.tab\Misc Tools.panel\Temp Phase.pushbutton"
$rootBinDir = Join-Path $repoRoot "bin"
$targetVersions = if ($RevitVersion -eq "All") { @("2025", "2026") } else { @($RevitVersion) }

function Get-DotNetPath {
    $dotnetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
    if ($dotnetCommand) {
        return $dotnetCommand.Source
    }

    $programFilesDotNet = Join-Path ${env:ProgramFiles} "dotnet\dotnet.exe"
    if (Test-Path -LiteralPath $programFilesDotNet -PathType Leaf) {
        return $programFilesDotNet
    }

    throw "dotnet SDK was not found. Install the .NET SDK before building Temp Phase controller DLLs."
}

function Get-AssemblyIdentity([string] $assemblyPath) {
    try {
        return [System.Reflection.AssemblyName]::GetAssemblyName($assemblyPath)
    }
    catch {
        throw "Could not read .NET assembly metadata from '$assemblyPath': $($_.Exception.Message)"
    }
}

function Assert-FileContains([string] $path, [string] $needle, [string] $description) {
    $content = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    if ($content.IndexOf($needle, [System.StringComparison]::Ordinal) -lt 0) {
        throw "$description is missing required text '$needle': $path"
    }
}

function Assert-FileNotContains([string] $path, [string] $needle, [string] $description) {
    $content = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    if ($content.IndexOf($needle, [System.StringComparison]::Ordinal) -ge 0) {
        throw "$description must not contain '$needle': $path"
    }
}

function Assert-NoRevitApiAssemblies {
    if (-not (Test-Path -LiteralPath $rootBinDir -PathType Container)) {
        return
    }

    $apiAssemblies = @(Get-ChildItem -LiteralPath $rootBinDir -Filter "RevitAPI*.dll" -File -ErrorAction SilentlyContinue)
    if ($apiAssemblies.Count -gt 0) {
        $names = ($apiAssemblies | ForEach-Object { $_.FullName }) -join ", "
        throw "Revit API assemblies must not be shipped in EasyBIM bin: $names"
    }
}

function Assert-SourcePackage {
    $bundleFile = Join-Path $sourceBundleDir "bundle.yaml"
    $scriptFile = Join-Path $sourceBundleDir "script.py"
    $removedScriptFile = Join-Path $sourceBundleDir "script.cs"
    $controllerFile = Join-Path $repoRoot "src\TempPhase\TempPhase.RevitCommon\TempPhaseController.cs"

    if (-not (Test-Path -LiteralPath $bundleFile -PathType Leaf)) {
        throw "Temp Phase bundle.yaml was not found: $bundleFile"
    }

    if (-not (Test-Path -LiteralPath $scriptFile -PathType Leaf)) {
        throw "Temp Phase script.py was not found: $scriptFile"
    }

    if (Test-Path -LiteralPath $removedScriptFile -PathType Leaf) {
        throw "Temp Phase must use the Python command wrapper; remove: $removedScriptFile"
    }

    if (-not (Test-Path -LiteralPath $controllerFile -PathType Leaf)) {
        throw "Temp Phase controller source was not found: $controllerFile"
    }

    Assert-FileNotContains $bundleFile "modules:" "Temp Phase bundle metadata"
    Assert-FileContains $scriptFile "from easybim import temp_phase_view" "Temp Phase Python command wrapper"
    Assert-FileContains $scriptFile "run_pushbutton" "Temp Phase Python command wrapper"
    Assert-FileNotContains $scriptFile "TempPhaseController" "Temp Phase Python command wrapper"

    foreach ($hookName in @("doc-closing.py", "app-idling.py", "doc-closed.py")) {
        $hookPath = Join-Path $repoRoot "hooks\$hookName"
        if (-not (Test-Path -LiteralPath $hookPath -PathType Leaf)) {
            throw "Python Temp Phase hook was not found: $hookPath"
        }

        Assert-FileContains $hookPath "temp_phase_close" "Temp Phase hook $hookName"
        Assert-FileNotContains $hookPath "TempPhaseController" "Temp Phase hook $hookName"
    }
}

function Get-ProjectPath([string] $version) {
    return Join-Path $repoRoot ("src\TempPhase\TempPhase.Revit{0}\TempPhase.Revit{0}.csproj" -f $version)
}

function Get-BuildArtifactPath([string] $version) {
    return Join-Path $repoRoot ("src\TempPhase\TempPhase.Revit{0}\bin\{1}\net8.0-windows\TempPhaseController.Revit{0}.dll" -f $version, $configuration)
}

function Get-TrackedControllerPath([string] $version) {
    return Join-Path $rootBinDir ("TempPhaseController.Revit{0}.dll" -f $version)
}

function Assert-ControllerIdentity([string] $controllerFile, [string] $version, [switch] $Required) {
    $expectedName = "TempPhaseController.Revit$version"

    if (-not (Test-Path -LiteralPath $controllerFile -PathType Leaf)) {
        if ($Required) {
            throw "Temp Phase controller DLL was not found: $controllerFile"
        }
        return
    }

    $identity = Get-AssemblyIdentity $controllerFile
    if ($identity.Name -ne $expectedName) {
        throw "Controller assembly is '$($identity.Name)', but expected '$expectedName'. Path: $controllerFile"
    }
}

function Assert-FileHashMatches([string] $source, [string] $destination, [string] $description) {
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $destinationHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
    if ($sourceHash -ne $destinationHash) {
        throw "$description copy verification failed. Source: $source Destination: $destination"
    }
}

function Build-Controller([string] $version) {
    $project = Get-ProjectPath $version
    if (-not (Test-Path -LiteralPath $project -PathType Leaf)) {
        throw "Temp Phase project not found: $project"
    }

    $dotnetPath = Get-DotNetPath
    Write-Host ("Building Temp Phase controller for Revit {0}" -f $version)
    $buildArgs = @("build", $project, "--configuration", $configuration, "--nologo")
    & $dotnetPath @buildArgs
    $buildExitCode = $LASTEXITCODE
    if ($null -ne $buildExitCode -and $buildExitCode -ne 0) {
        throw "Temp Phase Revit $version build failed."
    }

    $artifact = Get-BuildArtifactPath $version
    Assert-ControllerIdentity $artifact $version -Required
    return $artifact
}

function Update-TrackedController([string] $version, [string] $artifact) {
    New-Item -ItemType Directory -Path $rootBinDir -Force | Out-Null
    $destination = Get-TrackedControllerPath $version
    Copy-Item -LiteralPath $artifact -Destination $destination -Force
    Assert-FileHashMatches $artifact $destination "Temp Phase controller Revit $version"
    Assert-ControllerIdentity $destination $version -Required
    Write-Host ("Updated {0}" -f $destination)
}

function Write-ControllerStatus([string] $version) {
    $controllerPath = Get-TrackedControllerPath $version
    if (-not (Test-Path -LiteralPath $controllerPath -PathType Leaf)) {
        Write-Host ("Revit {0}: missing ({1})" -f $version, $controllerPath)
        return
    }

    $identity = Get-AssemblyIdentity $controllerPath
    Write-Host ("Revit {0}: {1} ({2})" -f $version, $identity.Name, $controllerPath)
}

Assert-SourcePackage
Assert-NoRevitApiAssemblies

if ($VerifyOnly) {
    foreach ($version in $targetVersions) {
        Assert-ControllerIdentity (Get-TrackedControllerPath $version) $version -Required
        Write-ControllerStatus $version
    }

    Write-Host "Temp Phase developer-only fallback controller verification succeeded."
    return
}

foreach ($version in $targetVersions) {
    $artifact = Build-Controller $version
    Update-TrackedController $version $artifact
}

Assert-NoRevitApiAssemblies
foreach ($version in $targetVersions) {
    Assert-ControllerIdentity (Get-TrackedControllerPath $version) $version -Required
    Write-ControllerStatus $version
}

Write-Host "Temp Phase developer-only fallback controller update succeeded."
