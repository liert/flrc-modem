# Start the WebUI with this repository's virtual environment.
# All arguments are forwarded, e.g. .\run.ps1 --port 8080 --no-browser.
$ErrorActionPreference = 'Stop'
$runExitCode = 0
$webuiArguments = @($args)

Push-Location -LiteralPath $PSScriptRoot
try {
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw 'The Python virtual environment is missing. Run install.bat or install.ps1 first.'
    }
    $showHelp = ($webuiArguments -contains '--help') -or ($webuiArguments -contains '-h')
    if (-not $showHelp -and -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'webui\frontend\dist\index.html') -PathType Leaf)) {
        throw 'The WebUI frontend has not been built. Run install.bat or install.ps1 first.'
    }

    & $venvPython -m flrc_modem webui @webuiArguments
    $runExitCode = $LASTEXITCODE
}
catch {
    [Console]::Error.WriteLine("Startup failed: $($_.Exception.Message)")
    $runExitCode = 1
}
finally {
    Pop-Location
}
exit $runExitCode
