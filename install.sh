#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${HOME}/.agents/skills/ros2-engineering-skills"
MODE="copy"
FORCE=0
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage: ./install.sh [OPTIONS]

Install this checkout as an Agent Skill.

Options:
  --target PATH  Installation path
  --link         Create a symbolic link instead of copying files
  --force        Replace an existing target
  --dry-run      Print the planned operation without changing files
  -h, --help     Show this help

Symbolic-link targets are never replaced; pass the resolved path instead.
USAGE
}

while (($#)); do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || { echo "error: --target requires a path" >&2; exit 2; }
      TARGET="$2"
      shift 2
      ;;
    --link)
      MODE="link"
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

TARGET="$(python3 - "$TARGET" <<'PY'
import os
import sys
print(os.path.abspath(os.path.expanduser(sys.argv[1])))
PY
)"
HOME_DIR="$(python3 - <<'PY'
import os
print(os.path.abspath(os.path.expanduser('~')))
PY
)"

case "$TARGET" in
  /|"$HOME_DIR"|"$ROOT_DIR"|"$ROOT_DIR"/*)
    echo "error: refusing unsafe target: $TARGET" >&2
    exit 2
    ;;
esac

if [[ -L "$TARGET" ]]; then
  resolved="$(python3 - "$TARGET" <<'PY'
import os
import sys
print(os.path.realpath(sys.argv[1]))
PY
)"
  echo "error: target is a symbolic link to $resolved; refusing to replace it." >&2
  echo "Re-run with --target $resolved to update the linked installation, or remove the link explicitly." >&2
  exit 1
fi

if [[ -e "$TARGET" ]]; then
  if [[ "$FORCE" -ne 1 ]]; then
    echo "error: target already exists: $TARGET (use --force to replace it)" >&2
    exit 1
  fi
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "mode=$MODE"
  echo "source=$ROOT_DIR"
  echo "target=$TARGET"
  echo "force=$FORCE"
  exit 0
fi

if [[ -e "$TARGET" ]]; then
  rm -rf -- "$TARGET"
fi
mkdir -p -- "$(dirname -- "$TARGET")"

if [[ "$MODE" == "link" ]]; then
  ln -s -- "$ROOT_DIR" "$TARGET"
else
  mkdir -p -- "$TARGET"
  tar -C "$ROOT_DIR" \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='.mypy_cache' \
    --exclude='.coverage' \
    -cf - . | tar -C "$TARGET" -xf -
fi

[[ -f "$TARGET/SKILL.md" ]] || { echo "error: SKILL.md was not installed" >&2; exit 1; }
[[ -d "$TARGET/references" ]] || { echo "error: references/ was not installed" >&2; exit 1; }
grep -q '^name: ros2-engineering-skills$' "$TARGET/SKILL.md" || {
  echo "error: installed SKILL.md has an unexpected name" >&2
  exit 1
}

echo "installed ros2-engineering-skills at $TARGET ($MODE)"
