[CmdletBinding()]
param(
    [string]$Target = (Join-Path $HOME '.agents/skills/ros2-engineering-skills'),
    [switch]$Link,
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath($PSScriptRoot)
if ($Target.StartsWith('~')) {
    $Target = Join-Path $HOME $Target.Substring(1).TrimStart('/', '\')
}
$Target = [System.IO.Path]::GetFullPath($Target)
$HomePath = [System.IO.Path]::GetFullPath($HOME)
$PathRoot = [System.IO.Path]::GetPathRoot($Target)

if ($Target -eq $Root -or
    $Target.StartsWith(
        $Root + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    $Target -eq $HomePath -or $Target -eq $PathRoot) {
    throw "Refusing unsafe target: $Target"
}

# Get-Item rather than Test-Path: a dangling symbolic link or junction can
# report as absent while still occupying the path. Reparse points are never
# replaced, so a link shared by several clients cannot be split silently.
$TargetItem = Get-Item -LiteralPath $Target -Force -ErrorAction SilentlyContinue
if ($null -ne $TargetItem -and
    ($TargetItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
    $Resolved = if ($TargetItem.Target) { $TargetItem.Target | Select-Object -First 1 } else { '<unknown>' }
    throw "Target is a symbolic link or reparse point to ${Resolved}: $Target; refusing to replace it. " +
        "Re-run with -Target <resolved path> to update the linked installation, or remove the link explicitly."
}
$Exists = $null -ne $TargetItem
if ($Exists -and -not $Force) {
    throw "Target already exists: $Target (use -Force to replace it)"
}
# -Force replaces a previous installation of this skill and nothing else. The
# Remove-Item -Recurse below would otherwise delete a mistyped parent such as
# ~/.claude/skills or ~/.claude (settings, transcripts, other skills). The same
# judgement applies to -DryRun so a rehearsal cannot pass where the real run
# would refuse.
if ($Exists) {
    $Leaf = Split-Path -Leaf $Target
    $ClientDirs = @('skills', '.claude', '.codex', '.cursor', '.gemini', '.agents', '.config')
    if ($ClientDirs -contains $Leaf.ToLowerInvariant() -or
        ((Split-Path -Parent $Target) -eq $HomePath -and $Leaf.StartsWith('.'))) {
        throw "Refusing to replace ${Target}: client configuration directory"
    }
    $ExistingSkill = Join-Path $Target 'SKILL.md'
    $ExistingReferences = Join-Path $Target 'references'
    if (-not $TargetItem.PSIsContainer -or
        -not (Test-Path -LiteralPath $ExistingSkill -PathType Leaf) -or
        -not (Test-Path -LiteralPath $ExistingReferences -PathType Container) -or
        -not (Select-String -LiteralPath $ExistingSkill -Pattern '^name: ros2-engineering-skills$' -Quiet)) {
        throw "Refusing to replace ${Target}: not an existing ros2-engineering-skills installation"
    }
    $Manifest = Join-Path $Target 'INSTALL_PROVENANCE.json'
    if (Test-Path -LiteralPath $Manifest -PathType Leaf) {
        # Must equal PROVENANCE_SCHEMA in scripts/install_skill.py (a test
        # keeps the two in step).
        $ProvenanceSchema = 1
        $Described = $false
        try {
            $Raw = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8
            # ConvertFrom-Json unrolls a one-element array into its object, so
            # the raw text decides that the top level is a single object.
            if ($null -ne $Raw -and $Raw.TrimStart().StartsWith('{')) {
                $Provenance = $Raw | ConvertFrom-Json
                $Version = $Provenance.schema_version
                # Exact types: -eq converts to the left operand's type, so a
                # string "1" or JSON true would otherwise pass for 1.
                $Described = ($Provenance -is [System.Management.Automation.PSCustomObject]) -and
                    ($Provenance.skill_name -is [string]) -and
                    ($Provenance.skill_name -ceq 'ros2-engineering-skills') -and
                    ($Version -is [int] -or $Version -is [long]) -and
                    ($Version -eq $ProvenanceSchema)
            }
        }
        catch {
            $Described = $false
        }
        if (-not $Described) {
            throw "Refusing to replace ${Target}: provenance manifest does not describe this skill"
        }
    }
}

$Mode = if ($Link) { 'link' } else { 'copy' }
if ($DryRun) {
    Write-Output "mode=$Mode"
    Write-Output "source=$Root"
    Write-Output "target=$Target"
    Write-Output "force=$($Force.IsPresent)"
    exit 0
}

if ($Exists) {
    Remove-Item -LiteralPath $Target -Recurse -Force
}
$Parent = Split-Path -Parent $Target
New-Item -ItemType Directory -Path $Parent -Force | Out-Null

if ($Link) {
    New-Item -ItemType SymbolicLink -Path $Target -Target $Root | Out-Null
}
else {
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
    $Excluded = @(
        '.git', '.venv', 'venv', '__pycache__',
        '.pytest_cache', '.mypy_cache', '.coverage'
    )
    Get-ChildItem -LiteralPath $Root -Force |
        Where-Object { $Excluded -notcontains $_.Name } |
        Copy-Item -Destination $Target -Recurse -Force
}

$Skill = Join-Path $Target 'SKILL.md'
$References = Join-Path $Target 'references'
if (-not (Test-Path -LiteralPath $Skill -PathType Leaf)) {
    throw 'SKILL.md was not installed'
}
if (-not (Test-Path -LiteralPath $References -PathType Container)) {
    throw 'references/ was not installed'
}
if (-not (Select-String -LiteralPath $Skill -Pattern '^name: ros2-engineering-skills$' -Quiet)) {
    throw 'Installed SKILL.md has an unexpected name'
}

Write-Output "installed ros2-engineering-skills at $Target ($Mode)"
if ($Link) {
    Write-Output @"
note: the linked checkout contains .claude-plugin/plugin.json and hooks/hooks.json,
so a client that scans $Target for plugins may register its PreToolUse and Stop
hooks. Whether that happens depends on the client and its version; this installer
does not configure hooks. Restart running sessions after changing the link, and
use scripts/install_skill.py for a hook-free knowledge-only copy.
"@
}
