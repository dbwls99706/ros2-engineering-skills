# Expected: Latched Localization Recovery

## Required elements

### Separate current observation from failure history
- State that the vendor pose is currently fresh/stable while the historical loss
  invalidated continuity and left `LOST` latched.
- Report current observation, failure time/cause, latched reason, why the latch
  persists, and its explicit clear/transition condition separately.

### Do not clear the latch from freshness alone
- Freshness after a gap does not prove pose continuity, command ownership, or that
  the robot remained where the old command assumes.
- Identify the failure class: freshness gap, session/generation change, jump, or other
  localization discontinuity as available from evidence.

### Design recovery evidence
- Verify the command path is stopped/owned as expected and, when the recovery assumes
  it, that the robot is stationary.
- Re-establish current pose/continuity with an independent localization observation,
  map registration, anchor, or other project-appropriate evidence.
- Preserve the old failed proof as history instead of silently reviving it.

### Prevent stale-command replay
- Invalidate the pre-loss navigation goal/command by default.
- After recovery, require a fresh command/goal unless a separately designed and
  validated resume protocol explicitly permits continuation.
- Do not claim that a successful recovery check establishes broader field readiness.
