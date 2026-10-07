# PostToolUse hook: runs after Claude edits or writes a file.
# Formats the file, then lints it. Lint errors are sent back to Claude
# (exit code 2 shows stderr to Claude) so it fixes them in the same turn.
# Does nothing until the relevant project (services/api or apps/web) exists.

$hookInput = [Console]::In.ReadToEnd() | ConvertFrom-Json
$path = $hookInput.tool_input.file_path
if (-not $path) { exit 0 }

$root = $env:CLAUDE_PROJECT_DIR

# Ruff matches per-file-ignores against the project path; a lowercase drive
# letter ("c:\...") makes those globs silently miss, so normalise both paths.
function Set-DriveUpper([string]$p) {
    if ($p -match '^[a-z]:') { return $p.Substring(0, 1).ToUpper() + $p.Substring(1) }
    return $p
}
$root = Set-DriveUpper $root
$path = Set-DriveUpper $path

# An activated conda/venv elsewhere makes uv print a warning on every run.
$env:VIRTUAL_ENV = $null

$normalized = $path -replace '\\', '/'

# --- Python: services/api ---------------------------------------------------
if ($normalized -match '/services/api/.*\.py$') {
    $api = Join-Path $root 'services/api'
    if (-not (Test-Path (Join-Path $api 'pyproject.toml'))) { exit 0 }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { exit 0 }

    uv run --directory $api ruff format $path *> $null
    $lint = uv run --directory $api ruff check --fix $path 2>&1
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("ruff reported problems in ${path}:")
        [Console]::Error.WriteLine(($lint | Out-String))
        exit 2
    }
    exit 0
}

# --- TypeScript / web: apps/web ---------------------------------------------
if ($normalized -match '/apps/web/.*\.(ts|tsx|js|jsx|css|json)$') {
    $web = Join-Path $root 'apps/web'
    if (-not (Test-Path (Join-Path $web 'node_modules'))) { exit 0 }
    if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) { exit 0 }

    pnpm --dir $web exec prettier --write $path *> $null

    if ($normalized -match '\.(ts|tsx|js|jsx)$') {
        $lint = pnpm --dir $web exec eslint $path 2>&1
        if ($LASTEXITCODE -ne 0) {
            [Console]::Error.WriteLine("eslint reported problems in ${path}:")
            [Console]::Error.WriteLine(($lint | Out-String))
            exit 2
        }
    }
    exit 0
}

exit 0
