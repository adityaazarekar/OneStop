# Dot-source before any install/run command:  . .\scripts\dev-env.ps1
# Keeps every cache, browser binary and temp file inside the project folder (never C:).
$Root = Split-Path -Parent $PSScriptRoot
$Tool = Join-Path $Root ".toolchain"
foreach ($d in "pip-cache", "tmp", "ms-playwright", "crawl4ai", "hf-cache", "xdg-cache", "tldextract") {
    New-Item -ItemType Directory -Force -Path (Join-Path $Tool $d) | Out-Null
}
$env:PIP_CACHE_DIR = Join-Path $Tool "pip-cache"
$env:TEMP = Join-Path $Tool "tmp"
$env:TMP = $env:TEMP
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Tool "ms-playwright"
$env:CRAWL4_AI_BASE_DIRECTORY = Join-Path $Tool "crawl4ai"
$env:HF_HOME = Join-Path $Tool "hf-cache"
$env:XDG_CACHE_HOME = Join-Path $Tool "xdg-cache"
$env:TLDEXTRACT_CACHE = Join-Path $Tool "tldextract"
$env:PYTHONPATH = Join-Path $Root "backend"
$Py = Join-Path $Root "backend\.venv\Scripts\python.exe"
$env:PATH = (Join-Path $Root "backend\.venv\Scripts") + ";" + $env:PATH
Write-Host "OneStop toolchain -> $Tool (python: $Py)"
