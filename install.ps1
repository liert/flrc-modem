# Install the Python package and build the WebUI. Compatible with PowerShell 5.1+.
param(
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$installExitCode = 0

function Invoke-Checked {
    param([string]$Executable, [string[]]$CommandArguments)
    & $Executable @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Executable $($CommandArguments -join ' ')"
    }
}

Push-Location -LiteralPath $PSScriptRoot
try {
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $frontendPath = Join-Path $PSScriptRoot 'webui\frontend'
    $versionCheck = 'import sys; print(sys.version); sys.exit(0 if sys.version_info >= (3, 11) else 1)'

    if (-not $SkipFrontend) {
        $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
        $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if (-not $nodeCommand -or -not $npmCommand) {
            throw 'Node.js and npm are required to build the WebUI. Install Node.js 22 LTS or newer, reopen the terminal and retry; use -SkipFrontend for CLI only.'
        }
        Write-Host 'Checking Node.js (18+ required; 22 LTS or newer recommended)...'
        Invoke-Checked $nodeCommand.Source @('-e', 'process.exit(parseInt(process.versions.node, 10) >= 18 ? 0 : 1)')
    }

    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        Write-Host '[1/3] Reusing .venv (Python 3.11+ required)...'
        Invoke-Checked $venvPython @('-c', $versionCheck)
    }
    else {
        if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv')) {
            throw 'The existing .venv does not contain Scripts\python.exe. Repair it or move it aside before retrying.'
        }
        Write-Host '[1/3] Creating .venv...'
        $launcherCommand = Get-Command py.exe -ErrorAction SilentlyContinue
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($launcherCommand) {
            Invoke-Checked $launcherCommand.Source @('-3', '-c', $versionCheck)
            Invoke-Checked $launcherCommand.Source @('-3', '-m', 'venv', '.venv')
        }
        elseif ($pythonCommand) {
            Invoke-Checked $pythonCommand.Source @('-c', $versionCheck)
            Invoke-Checked $pythonCommand.Source @('-m', 'venv', '.venv')
        }
        else {
            throw 'Python 3.11+ was not found. Install Python with the Windows launcher or add python.exe to PATH, then retry.'
        }
    }

    Write-Host '[2/3] Installing Python dependencies (including Numba)...'
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '--upgrade', 'pip')
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '-e', '.')

    if ($SkipFrontend) {
        Write-Host '[3/3] Skipping WebUI frontend (-SkipFrontend).'
        Write-Host 'CLI ready: .\.venv\Scripts\python.exe -m flrc_modem --help'
    }
    else {
        Write-Host '[3/3] Installing frontend dependencies and building the WebUI...'
        Push-Location -LiteralPath $frontendPath
        try {
            Invoke-Checked $npmCommand.Source @('ci')
            Invoke-Checked $npmCommand.Source @('run', 'build')
        }
        finally {
            Pop-Location
        }
        Write-Host 'Installation complete. Start the WebUI with .\run.bat or .\run.ps1.'
    }
}
catch {
    [Console]::Error.WriteLine("Installation failed: $($_.Exception.Message)")
    $installExitCode = 1
}
finally {
    Pop-Location
}
exit $installExitCode
