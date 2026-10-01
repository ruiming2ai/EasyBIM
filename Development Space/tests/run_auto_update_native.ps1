# Isolated, off-host native API validation. Requires Windows PowerShell 5.1 and Git.
# Revit itself is NOT simulated or claimed tested by this script.
$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$probeRoot = Join-Path ([IO.Path]::GetTempPath()) ('easybim-native-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $probeRoot | Out-Null
$packages = @(
    @('libgit2sharp', '0.31.0', 'lib/net472'),
    @('libgit2sharp.nativebinaries', '2.0.323', 'runtimes/win-x64/native'),
    @('ironpython', '2.7.12', 'lib/net45'),
    @('dynamiclanguageruntime', '1.3.1', 'lib/net45'),
    @('ironpython.stdlib', '2.7.12', 'content/Lib')
)
foreach ($package in $packages) {
    $name, $version, $prefix = $package
    $zip = Join-Path $probeRoot ($name + '.zip')
    $extract = Join-Path $probeRoot $name
    Invoke-WebRequest -UseBasicParsing -Uri "https://api.nuget.org/v3-flatcontainer/$name/$version/$name.$version.nupkg" -OutFile $zip
    Expand-Archive -LiteralPath $zip -DestinationPath $extract
    $lib = Join-Path $extract $prefix
    if ($name -eq 'ironpython.stdlib') {
        Copy-Item -Recurse $lib (Join-Path $probeRoot 'stdlib')
    } else {
        Get-ChildItem $lib -Filter '*.dll' | Copy-Item -Destination $probeRoot
    }
}
function Invoke-Git {
    & git @args
    if ($LASTEXITCODE -ne 0) { throw "Git test fixture command failed: $args" }
}
$seed = Join-Path $probeRoot 'seed'
$remote = Join-Path $probeRoot 'remote.git'
$fixture = Join-Path $probeRoot 'installation'
Invoke-Git init -q -b main $seed
Invoke-Git -C $seed config user.name 'EasyBIM Native Test'
Invoke-Git -C $seed config user.email 'test@example.invalid'
Invoke-Git -C $seed config core.autocrlf false
$revisions = @()
foreach ($label in @('A', 'B', 'C')) {
    [IO.File]::WriteAllText((Join-Path $seed 'tracked.txt'), "Published $label`n")
    Invoke-Git -C $seed add tracked.txt
    Invoke-Git -C $seed commit -qm "Published $label"
    $revisions += (Invoke-Git -C $seed rev-parse HEAD)
}
Invoke-Git clone -q --bare $seed $remote
Invoke-Git --git-dir $remote update-ref refs/heads/main $revisions[0]
Invoke-Git -c core.autocrlf=false clone -q $remote $fixture
Invoke-Git -C $fixture config user.name 'EasyBIM Native Test'
Invoke-Git -C $fixture config user.email 'test@example.invalid'
Invoke-Git -C $fixture config core.autocrlf false
Invoke-Git -C $fixture commit -q --allow-empty -m 'Preexisting file-neutral history'
$env:PATH = $probeRoot + ';' + $env:PATH
foreach ($assembly in @('Microsoft.Scripting.dll','Microsoft.Dynamic.dll','IronPython.dll','IronPython.Modules.dll','LibGit2Sharp.dll')) {
    [Reflection.Assembly]::LoadFrom((Join-Path $probeRoot $assembly)) | Out-Null
}
$engine = [IronPython.Hosting.Python]::CreateEngine()
$paths = $engine.GetSearchPaths()
$paths.Add((Join-Path $probeRoot 'stdlib'))
$engine.SetSearchPaths($paths)
$scope = $engine.CreateScope()
$scope.SetVariable('probe_dir', $probeRoot)
$scope.SetVariable('fixture_dir', $fixture)
$scope.SetVariable('module_path', (Join-Path $sourceRoot 'lib/easybim/auto_update.py'))
$scope.SetVariable('revisions', $revisions)
$engine.ExecuteFile((Join-Path $PSScriptRoot 'native_auto_update_sessions.py'), $scope) | Out-Null
