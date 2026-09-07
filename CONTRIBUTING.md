# Contributing

Thank you for improving `ros2-engineering-skills`.

## Good contributions

The most valuable changes are:

- corrections backed by upstream documentation or installed-version evidence;
- reproducible ROS 2 failure cases;
- safer or more precise engineering guidance;
- tests that prevent a previously observed error from returning;
- focused improvements to the user-facing validators.

## Development setup

```bash
git clone https://github.com/dbwls99706/ros2-engineering-skills.git
cd ros2-engineering-skills
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

## Required checks

Run the checks relevant to the change, and run the full Python gate before a
pull request:

```bash
flake8 scripts/ tests/ examples/
mypy scripts/ --ignore-missing-imports
python -m pytest tests/ -v --tb=short \
  --cov=scripts --cov-report=term-missing --cov-fail-under=90
python scripts/eval_runner.py
```

For generated ROS 2 code, also build and test it in every distribution whose
behavior the change claims to support:

```bash
bash tests/run_ros2_tests.sh humble jazzy kilted lyrical rolling
```

The runner requires Linux, Docker with Buildx, and GNU `timeout`. It performs both
image creation and test execution, retains diagnostics at the printed path, and
returns nonzero on build or runtime failure. See [ROS CI](docs/ROS_CI.md).

## Documentation accuracy

When changing a distribution-sensitive claim, record:

- ROS 2 distribution;
- operating system;
- package name and installed version;
- source or shipped configuration that supports the claim;
- verification level reached;
- what was not tested.

Prefer conditional wording over universal claims when middleware, package
version, hardware, executor, or transport details affect the result.

## Skill structure

- Keep the root `SKILL.md` concise and under 500 lines.
- Put detailed implementation guidance in `references/`.
- Add new reference files to the decision router.
- Keep Agent Skills metadata portable.
- Put Claude Code plugin metadata in `.claude-plugin/` and hooks in `hooks/`.

## Safety-sensitive changes

Never automate first motion or destructive fault injection on real hardware.
Document simulation and hardware-isolation steps, conservative limits, operator
presence, and the independent stop path.

## Pull requests

Keep each pull request focused. Describe:

- the problem;
- the evidence supporting the change;
- files and behavior changed;
- tests run and their results;
- verification level and skipped levels.
