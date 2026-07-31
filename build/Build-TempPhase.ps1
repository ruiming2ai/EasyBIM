[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("2025", "2026")]
    [string] $RevitVersion,

    [string] $ExtensionRoot,

    [string] $ArtifactPath,

    [switch] $NoStage,

    [switch] $VerifyOnly
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$defaultLiveExtensionRoot = "C:\Users\RML\Documents\GitHub\EasyBIM.extension"

if ([string]::IsNullOrWhiteSpace($ExtensionRoot)) {
    $ExtensionRoot = $defaultLiveExtensionRoot
}

$project = Join-Path $repoRoot ("src\TempPhase\TempPhase.Revit{0}\TempPhase.Revit{0}.csproj" -f $RevitVersion)
$configuration = "Release"
$sourceBundleDir = Join-Path $repoRoot "EasyBIM.tab\Misc Tools.panel\Temp Phase.pushbutton"
$sourceHooksDir = Join-Path $repoRoot "hooks"
$bundleRelativePath = "EasyBIM.tab\Misc Tools.panel\Temp Phase.pushbutton"
$expectedArtifactName = "TempPhaseController.Revit$RevitVersion"

function Resolve-RequiredPath([string] $path, [string] $description) {
    try {
        return (Resolve-Path -LiteralPath $path -ErrorAction Stop).ProviderPath
    }
    catch {
        throw "$description does not exist: $path"
    }
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

function Assert-SourcePackage {
    $sourceBundleFile = Join-Path $sourceBundleDir "bundle.yaml"
    $sourceScriptFile = Join-Path $sourceBundleDir "script.cs"

    if (-not (Test-Path -LiteralPath $sourceBundleFile -PathType Leaf)) {
        throw "Source Temp Phase bundle.yaml was not found: $sourceBundleFile"
    }

    if (-not (Test-Path -LiteralPath $sourceScriptFile -PathType Leaf)) {
        throw "Source Temp Phase script.cs was not found: $sourceScriptFile"
    }

    Assert-FileContains $sourceBundleFile "TempPhaseController.dll" "Source Temp Phase bundle metadata"
    Assert-FileContains $sourceScriptFile "ModuleCandidatePath" "Source Temp Phase command wrapper"
    Assert-FileContains $sourceScriptFile "Assembly.LoadFrom" "Source Temp Phase command wrapper"
    Assert-FileContains $sourceScriptFile "ScriptPath" "Source Temp Phase command wrapper"

    foreach ($hookName in @("doc-closing.cs", "app-idling.cs", "doc-closed.cs")) {
        $hookPath = Join-Path $sourceHooksDir $hookName
        if (-not (Test-Path -LiteralPath $hookPath -PathType Leaf)) {
            throw "Source Temp Phase hook was not found: $hookPath"
        }
        Assert-FileContains $hookPath "FindControllerAssembly" "Source Temp Phase hook $hookName"
        Assert-FileContains $hookPath "Assembly.Load" "Source Temp Phase hook $hookName"
    }
}

function Assert-ExtensionRoot([string] $root) {
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw @"
The live EasyBIM extension root does not exist: $root

Create or attach the EasyBIM extension at this path, or pass -ExtensionRoot with the exact extension folder pyRevit loads.
The current visible Revit button is probably coming from a different extension clone.
"@
    }

    $extensionFile = Join-Path $root "Extension.yaml"
    if (-not (Test-Path -LiteralPath $extensionFile -PathType Leaf)) {
        throw "Extension.yaml was not found in the live extension root: $extensionFile"
    }

    $extensionMetadata = Get-Content -LiteralPath $extensionFile -Raw -Encoding UTF8
    if ($extensionMetadata -notmatch "(?m)^\s*name\s*:\s*EasyBIM\s*$") {
        throw "The live extension root is not named EasyBIM in Extension.yaml: $extensionFile"
    }
}

function Assert-NoRevitApiAssemblies([string[]] $directories) {
    foreach ($directory in $directories) {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            continue
        }

        $apiAssemblies = @(Get-ChildItem -LiteralPath $directory -Filter "RevitAPI*.dll" -File -ErrorAction SilentlyContinue)
        if ($apiAssemblies.Count -gt 0) {
            $names = ($apiAssemblies | ForEach-Object { $_.FullName }) -join ", "
            throw "Revit API assemblies must not be copied beside TempPhaseController.dll: $names"
        }
    }
}

function Assert-ControllerIdentity([string] $controllerFile, [string] $label, [switch] $Required) {
    if (-not (Test-Path -LiteralPath $controllerFile -PathType Leaf)) {
        if ($Required) {
            throw "$label TempPhaseController.dll was not found: $controllerFile"
        }
        return
    }

    $identity = Get-AssemblyIdentity $controllerFile
    if ($identity.Name -ne $expectedArtifactName) {
        throw "$label controller assembly is '$($identity.Name)', but Revit $RevitVersion requires '$expectedArtifactName'. Path: $controllerFile"
    }
}

function Write-ControllerStatus([string] $controllerFile, [string] $label) {
    if (-not (Test-Path -LiteralPath $controllerFile -PathType Leaf)) {
        Write-Host ("{0}: missing ({1})" -f $label, $controllerFile)
        return
    }

    $identity = Get-AssemblyIdentity $controllerFile
    Write-Host ("{0}: {1} ({2})" -f $label, $identity.Name, $controllerFile)
}

function Assert-LivePackage([string] $root, [switch] $RequireController) {
    $bundleDir = Join-Path $root $bundleRelativePath
    $bundleFile = Join-Path $bundleDir "bundle.yaml"
    $scriptFile = Join-Path $bundleDir "script.cs"
    $commandBinDir = Join-Path $bundleDir "bin"
    $rootBinDir = Join-Path $root "bin"
    $commandController = Join-Path $commandBinDir "TempPhaseController.dll"
    $rootController = Join-Path $rootBinDir "TempPhaseController.dll"

    if (-not (Test-Path -LiteralPath $bundleFile -PathType Leaf)) {
        throw "EasyBIM Temp Phase bundle.yaml was not found in the live package: $bundleFile"
    }

    if (-not (Test-Path -LiteralPath $scriptFile -PathType Leaf)) {
        throw "EasyBIM Temp Phase script.cs was not found in the live package: $scriptFile"
    }

    Assert-FileContains $bundleFile "TempPhaseController.dll" "Live Temp Phase bundle metadata"
    Assert-FileContains $scriptFile "ModuleCandidatePath" "Live Temp Phase command wrapper"
    Assert-FileContains $scriptFile "Assembly.LoadFrom" "Live Temp Phase command wrapper"
    Assert-FileContains $scriptFile "ScriptPath" "Live Temp Phase command wrapper"

    foreach ($hookName in @("doc-closing.cs", "app-idling.cs", "doc-closed.cs")) {
        $hookPath = Join-Path (Join-Path $root "hooks") $hookName
        if (-not (Test-Path -LiteralPath $hookPath -PathType Leaf)) {
            throw "Live Temp Phase hook was not found: $hookPath"
        }
        Assert-FileContains $hookPath "FindControllerAssembly" "Live Temp Phase hook $hookName"
        Assert-FileContains $hookPath "Assembly.Load" "Live Temp Phase hook $hookName"
    }

    Assert-NoRevitApiAssemblies @($commandBinDir, $rootBinDir)
    Assert-ControllerIdentity $commandController "Command bundle" -Required:$RequireController
    Assert-ControllerIdentity $rootController "Extension root bin" -Required:$RequireController
}

function Write-PackageStatus([string] $root) {
    $bundleDir = Join-Path $root $bundleRelativePath
    $commandController = Join-Path (Join-Path $bundleDir "bin") "TempPhaseController.dll"
    $rootController = Join-Path (Join-Path $root "bin") "TempPhaseController.dll"

    Write-Host ("Source repository: {0}" -f $repoRoot)
    Write-Host ("Live extension root: {0}" -f $root)
    Write-Host ("Expected Revit module: {0}" -f $expectedArtifactName)
    Write-Host ("Command bundle: {0}" -f $bundleDir)
    Write-ControllerStatus $commandController "Command bundle module"
    Write-ControllerStatus $rootController "Extension root module"

    foreach ($hookName in @("doc-closing.cs", "app-idling.cs", "doc-closed.cs")) {
        $hookPath = Join-Path (Join-Path $root "hooks") $hookName
        $status = if (Test-Path -LiteralPath $hookPath -PathType Leaf) { "present" } else { "missing" }
        Write-Host ("Hook {0}: {1} ({2})" -f $hookName, $status, $hookPath)
    }
}

function Copy-IfDifferentPath([string] $source, [string] $destination) {
    $resolvedSource = (Resolve-Path -LiteralPath $source -ErrorAction Stop).ProviderPath
    $resolvedDestination = $null
    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        $resolvedDestination = (Resolve-Path -LiteralPath $destination -ErrorAction Stop).ProviderPath
    }

    if ($resolvedDestination -and [string]::Equals($resolvedSource, $resolvedDestination, [System.StringComparison]::OrdinalIgnoreCase)) {
        return
    }

    Copy-Item -LiteralPath $source -Destination $destination -Force
}

function Stage-PackageFiles([string] $root, [string] $artifact) {
    $targetBundleDir = Join-Path $root $bundleRelativePath
    $targetHooksDir = Join-Path $root "hooks"
    $targetCommandBinDir = Join-Path $targetBundleDir "bin"
    $targetRootBinDir = Join-Path $root "bin"

    New-Item -ItemType Directory -Path $targetBundleDir -Force | Out-Null
    New-Item -ItemType Directory -Path $targetHooksDir -Force | Out-Null
    New-Item -ItemType Directory -Path $targetCommandBinDir -Force | Out-Null
    New-Item -ItemType Directory -Path $targetRootBinDir -Force | Out-Null

    Copy-IfDifferentPath (Join-Path $sourceBundleDir "bundle.yaml") (Join-Path $targetBundleDir "bundle.yaml")
    Copy-IfDifferentPath (Join-Path $sourceBundleDir "script.cs") (Join-Path $targetBundleDir "script.cs")

    foreach ($hookName in @("doc-closing.cs", "app-idling.cs", "doc-closed.cs")) {
        Copy-IfDifferentPath (Join-Path $sourceHooksDir $hookName) (Join-Path $targetHooksDir $hookName)
    }

    Copy-Item -LiteralPath $artifact -Destination (Join-Path $targetCommandBinDir "TempPhaseController.dll") -Force
    Copy-Item -LiteralPath $artifact -Destination (Join-Path $targetRootBinDir "TempPhaseController.dll") -Force
}

Assert-SourcePackage

$resolvedExtensionRoot = $null
if (-not $NoStage -or $VerifyOnly) {
    Assert-ExtensionRoot $ExtensionRoot
    $resolvedExtensionRoot = (Resolve-Path -LiteralPath $ExtensionRoot -ErrorAction Stop).ProviderPath
}

if ($VerifyOnly) {
    Write-PackageStatus $resolvedExtensionRoot
    Assert-LivePackage $resolvedExtensionRoot -RequireController
    Write-Host ("Package preflight succeeded for Revit {0}." -f $RevitVersion)
    return
}

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
    $buildArgs = @("build", $project, "--configuration", $configuration, "--nologo")
    $buildProcess = Start-Process -FilePath $dotnetPath -ArgumentList $buildArgs -NoNewWindow -Wait -PassThru
    if ($buildProcess.ExitCode -ne 0) {
        throw "Temp Phase Revit $RevitVersion build failed."
    }

    $artifact = Join-Path $repoRoot ("src\TempPhase\TempPhase.Revit{0}\bin\{1}\net8.0-windows\TempPhaseController.Revit{0}.dll" -f $RevitVersion, $configuration)
}
else {
    $artifact = Resolve-RequiredPath $ArtifactPath "The prebuilt Temp Phase artifact"
    Write-Host ("Using prebuilt Temp Phase artifact: {0}" -f $artifact)
}

if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
    throw "Temp Phase artifact was not found: $artifact"
}

$artifactIdentity = Get-AssemblyIdentity $artifact
if ($artifactIdentity.Name -ne $expectedArtifactName) {
    throw "The build produced assembly '$($artifactIdentity.Name)', but expected '$expectedArtifactName'. Path: $artifact"
}

if ($NoStage) {
    Write-Host "NoStage specified; the live extension was not changed."
    Write-Host ("Built {0}" -f $artifact)
    return
}

Stage-PackageFiles $resolvedExtensionRoot $artifact
Assert-LivePackage $resolvedExtensionRoot -RequireController
Write-PackageStatus $resolvedExtensionRoot
Write-Host ("Staged Revit {0} Temp Phase package into {1}" -f $RevitVersion, $resolvedExtensionRoot)
Write-Host ("Built {0}" -f $artifact)
