<#
.SYNOPSIS
Run optional Gate research experiments.
.DESCRIPTION
Provides historical audit versions, query modes, frozen schemas, variant review
and candidate deduplication. Normal project runs use evaluation/run_live.ps1.
Execution calls the paid model API.
#>
param(
    [string]$PythonPath,
    [string[]]$Configs = @('baseline', 'query', 'product', 'full'),
    [int]$MaxSteps = 20,
    [int]$Repeats = 1,
    [int]$Seed = 42,
    [string]$Model = 'deepseek-chat',
    [string]$Dataset = 'evaluation/webshop_test_100.json',
    [string]$OutputDir = 'results/cz-v1-live',
    [ValidateSet('coverage', 'layered', 'direction')][string]$QueryMode = 'coverage',
    [ValidateSet('legacy', 'benchmark', 'variant', 'grounded')][string]$AuditVersion = 'grounded',
    [string]$FrozenSchemas,
    [switch]$VariantReview,
    [switch]$DeduplicateCandidates,
    [switch]$Resume,
    [switch]$DirectApi
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $candidatePython = Join-Path $env:USERPROFILE 'miniconda3\envs\webshop\python.exe'
    $PythonPath = if (Test-Path -LiteralPath $candidatePython) { $candidatePython } else { (Get-Command python).Source }
}
$environmentRoot = Split-Path -Parent $PythonPath
$javaCandidate = Join-Path $environmentRoot 'Library\lib\jvm'
$previousJava = $env:JAVA_HOME
$previousPath = $env:PATH
$previousNoProxy = $env:NO_PROXY
$previousEncoding = $env:PYTHONIOENCODING
Push-Location $projectRoot
try {
    if (Test-Path -LiteralPath (Join-Path $javaCandidate 'bin\server\jvm.dll')) {
        $env:JAVA_HOME = $javaCandidate
        $env:PATH = "$javaCandidate\bin;$environmentRoot\Library\bin;$env:PATH"
    }
    $env:PYTHONIOENCODING = 'utf-8'
    if ($DirectApi) {
        $apiBase = if ($env:DEEPSEEK_BASE_URL) { $env:DEEPSEEK_BASE_URL } else { 'https://api.deepseek.com' }
        $apiHostname = ([uri]$apiBase).Host
        $env:NO_PROXY = (@($previousNoProxy, $apiHostname) | Where-Object { $_ }) -join ','
    }
    & $PythonPath -m evaluation.preflight --num-products 1000
    if ($LASTEXITCODE -ne 0) { throw 'WebShop prerequisite check failed.' }
    $runArguments = @('-m', 'evaluation.run_experiment', '--configs') + $Configs + @(
        '--dataset', $Dataset, '--model', $Model, '--max-steps', "$MaxSteps",
        '--num-products', '1000', '--repeats', "$Repeats", '--seed', "$Seed", '--output-dir', $OutputDir,
        '--query-mode', $QueryMode, '--audit-version', $AuditVersion
    )
    if ($FrozenSchemas) { $runArguments += @('--frozen-schemas', $FrozenSchemas) }
    if ($VariantReview) { $runArguments += '--variant-review' }
    if ($DeduplicateCandidates) { $runArguments += '--deduplicate-candidates' }
    if ($Resume) { $runArguments += '--resume' }
    & $PythonPath @runArguments
    if ($LASTEXITCODE -ne 0) { throw 'WebShop experiment failed; inspect the saved records.' }
}
finally {
    $env:JAVA_HOME = $previousJava
    $env:PATH = $previousPath
    $env:NO_PROXY = $previousNoProxy
    $env:PYTHONIOENCODING = $previousEncoding
    Pop-Location
}
