<#
.SYNOPSIS
Run the four proposal configurations with the shared ReAct policy.
.DESCRIPTION
Uses coverage Query Gate and grounded Product Gate. Experimental interventions
are disabled. Calls the paid model API when executed; no manual labels required.
Advanced experiments use evaluation/run_live_advanced.ps1.
.EXAMPLE
.\evaluation\run_live.ps1 -MaxSteps 20 -OutputDir results/webshop -DirectApi
#>
[CmdletBinding()]
param(
    [string]$PythonPath,
    [ValidateSet('baseline', 'query', 'product', 'full')]
    [string[]]$Configs = @('baseline', 'query', 'product', 'full'),
    [ValidateRange(1, 2147483647)]
    [int]$MaxSteps = 20,
    [ValidateRange(1, 2147483647)]
    [int]$Repeats = 1,
    [string]$Model = 'deepseek-chat',
    [string]$Dataset = 'evaluation/webshop_test_100.json',
    [string]$OutputDir = 'results/webshop',
    [switch]$Resume,
    [switch]$DirectApi
)

$ErrorActionPreference = 'Stop'
# Keep the environment setup and runner shared with the advanced entry point.
# The formal entry deliberately exposes only the proposal's four configurations.
$runParameters = @{
    PythonPath = $PythonPath
    Configs = $Configs
    MaxSteps = $MaxSteps
    Repeats = $Repeats
    Seed = 42
    Model = $Model
    Dataset = $Dataset
    OutputDir = $OutputDir
    QueryMode = 'coverage'
    AuditVersion = 'grounded'
    Resume = $Resume
    DirectApi = $DirectApi
}
& (Join-Path $PSScriptRoot 'run_live_advanced.ps1') @runParameters
