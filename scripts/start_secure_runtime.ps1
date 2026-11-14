param([switch]$NoBrowser)
# Backward-compatible entry point for the unified launcher.
& (Join-Path $PSScriptRoot 'chatbi.ps1') restart -NoBrowser:$NoBrowser
