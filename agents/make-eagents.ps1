# Regenerate all per-subplatform eagents and their machine-readable specs.
#
# The previous version of this script copied one identical template into every
# agents/eagents/*.md file, which is why no agent knew how to initialize its
# component. Generation now lives in scripts/generate-agents.js and derives
# capabilities, toolchains, env vars and rules from each component's manifests.
#
# The generated eagents are bound to the immutable constitution by SHA-256 and
# validated by orchestrator/agent_specs.py (GET /agents/validate).

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    node scripts/generate-agents.js
    if ($LASTEXITCODE -ne 0) { throw "agent generation failed with exit $LASTEXITCODE" }
}
finally {
    Pop-Location
}
