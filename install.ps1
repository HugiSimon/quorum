# Installs quorum as a command, from nothing. Windows.
#
#   irm https://raw.githubusercontent.com/HugiSimon/quorum/master/install.ps1 | iex
#
# It installs uv if it is missing, then quorum as a tool: its own isolated environment,
# a `quorum` command in ~\.local\bin. Nothing is compiled.
$ErrorActionPreference = "Stop"

$repo = if ($env:QUORUM_REPO) { $env:QUORUM_REPO } else { "https://github.com/HugiSimon/quorum" }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "· uv is missing — installing it"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "· installing quorum from $repo"
uv tool install --force "git+$repo"
uv tool update-shell

Write-Host ""
Write-Host "quorum is installed. Open a new terminal, go into a project and type: quorum"
Write-Host "It needs an ACP agent: gemini --acp, or npx @zed-industries/claude-code-acp."
