#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/deploy/make_world_bundle.sh [--dry-run] [--output-dir DIR] [--worlds-dir DIR]
EOF
}

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT_DIR="$PROJECT_ROOT"
WORLDS_INPUT="$PROJECT_ROOT/generated_worlds"
DRY_RUN=0
CURRENT_STEP="startup"
TMPDIR_CREATED=""
MANIFEST_NAME="DATABOSS_WORLD_BUNDLE_MANIFEST.sha256"
WORLD_TREE_FULLY_READABLE=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --output-dir)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --worlds-dir)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      WORLDS_INPUT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

step() {
  CURRENT_STEP="$1"
  printf '\n==> %s\n' "$CURRENT_STEP"
}

# The only executor. In --dry-run it prints and returns 0 without running, so
# preview and real execution share the same command path.
run_cmd() {
  printf '+ %s\n' "$*"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  bash -c "$*"
}

# In --dry-run a failed precondition is a warning so the operator still sees
# the whole sequence; in a real run it stays a hard error.
fail_or_warn() {
  local message="$1"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "WARN (dry-run, would be fatal): $message"
    return 0
  fi
  echo "ERROR: $message" >&2
  exit 1
}

on_error() {
  local rc=$?
  echo "ERROR: step ${CURRENT_STEP} failed (exit ${rc})."
  exit "$rc"
}

cleanup() {
  if [[ -n "$TMPDIR_CREATED" && -d "$TMPDIR_CREATED" ]]; then
    rm -rf "$TMPDIR_CREATED"
  fi
}

trap cleanup EXIT
trap on_error ERR

q() {
  printf '%q' "$1"
}

bytes_human() {
  local bytes="$1"
  if command -v numfmt >/dev/null 2>&1; then
    numfmt --to=iec-i --suffix=B "$bytes"
  else
    printf '%s bytes' "$bytes"
  fi
}

nearest_existing_parent() {
  local path="$1"
  while [[ ! -e "$path" ]]; do
    path="$(dirname "$path")"
  done
  printf '%s\n' "$path"
}

transform_escape() {
  printf '%s' "$1" | sed -e 's/[|&]/\\&/g'
}

write_manifest() {
  local worlds_dir="$1"
  local manifest_file="$2"
  local generated_at="$3"
  local file rel sha link target

  {
    printf '# DATABOSS world bundle manifest\n'
    printf '# generated_utc: %s\n' "$generated_at"
    printf '# archive_root: generated_worlds\n'
    printf '# regular files: sha256  path\n'
    while IFS= read -r -d '' file; do
      rel="${file#"$worlds_dir"/}"
      sha="$(sha256sum "$file" | awk '{print $1}')"
      printf '%s  generated_worlds/%s\n' "$sha" "$rel"
    done < <(find "$worlds_dir" -type f -print0 | sort -z)
    printf '# symlinks: SYMLINK path -> target\n'
    while IFS= read -r -d '' link; do
      rel="${link#"$worlds_dir"/}"
      target="$(readlink "$link")"
      printf 'SYMLINK generated_worlds/%s -> %s\n' "$rel" "$target"
    done < <(find "$worlds_dir" -type l -print0 | sort -z)
  } > "$manifest_file"
}

verify_symlinks_inside_source() {
  local worlds_dir="$1"
  local link rel target_real count
  count=0
  while IFS= read -r -d '' link; do
    count=$((count + 1))
    rel="${link#"$worlds_dir"/}"
    if ! target_real="$(realpath -e "$link" 2>/dev/null)"; then
      fail_or_warn "symlink generated_worlds/$rel has a missing target."
      continue
    fi
    case "$target_real" in
      "$worlds_dir"|"$worlds_dir"/*)
        ;;
      *)
        fail_or_warn "symlink generated_worlds/$rel resolves outside the worlds directory: $target_real"
        ;;
    esac
  done < <(find "$worlds_dir" -type l -print0 2>/dev/null | sort -z)
  if [[ "$WORLD_TREE_FULLY_READABLE" -eq 1 ]]; then
    echo "OK: preserving symlinks; checked ${count} symlink(s), all resolvable targets are inside $worlds_dir."
  else
    echo "WARN: preserving symlinks; checked ${count} symlink(s) in the readable part of $worlds_dir, but unreadable directories prevented a complete symlink audit."
  fi
}

step "1. Resolve inputs"
if ! WORLDS_DIR="$(realpath -e "$WORLDS_INPUT" 2>/dev/null)"; then
  fail_or_warn "worlds directory does not exist: $WORLDS_INPUT"
  WORLDS_DIR="$WORLDS_INPUT"
fi
if [[ ! -d "$WORLDS_DIR" ]]; then
  fail_or_warn "worlds path is not a directory: $WORLDS_INPUT"
fi
OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
UTC_DATE="$(date -u +%Y%m%d)"
GENERATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if command -v zstd >/dev/null 2>&1; then
  ARCHIVE_EXT="tar.zst"
  TAR_CREATE_FLAGS="-I zstd -cf"
  TAR_EXTRACT_FLAGS="-I zstd -xf"
else
  ARCHIVE_EXT="tar.gz"
  TAR_CREATE_FLAGS="-czf"
  TAR_EXTRACT_FLAGS="-xzf"
fi
ARCHIVE_BASENAME="databoss-worlds-${UTC_DATE}.${ARCHIVE_EXT}"
SHA_BASENAME="databoss-worlds-${UTC_DATE}.sha256"
ARCHIVE_PATH="$OUTPUT_DIR/$ARCHIVE_BASENAME"
SHA_PATH="$OUTPUT_DIR/$SHA_BASENAME"
WORLDS_PARENT="$(dirname "$WORLDS_DIR")"
WORLDS_BASE="$(basename "$WORLDS_DIR")"
SOURCE_TRANSFORM="s|^$(transform_escape "$WORLDS_BASE")|generated_worlds|"
MANIFEST_TRANSFORM="s|^$(transform_escape "$MANIFEST_NAME")|generated_worlds/$MANIFEST_NAME|"

echo "Worlds source: $WORLDS_DIR"
echo "Output archive: $ARCHIVE_PATH"
echo "Compression: $ARCHIVE_EXT"
if [[ -e "$ARCHIVE_PATH" ]]; then
  fail_or_warn "archive already exists and will not be overwritten: $ARCHIVE_PATH"
fi
if [[ -e "$SHA_PATH" ]]; then
  fail_or_warn "checksum already exists and will not be overwritten: $SHA_PATH"
fi

step "2. Check output space"
UNREADABLE_DIRS="$(find "$WORLDS_DIR" -type d ! -readable -print 2>/dev/null || true)"
if [[ -n "$UNREADABLE_DIRS" ]]; then
  WORLD_TREE_FULLY_READABLE=0
  fail_or_warn "worlds directory contains unreadable directories; first unreadable path: $(printf '%s\n' "$UNREADABLE_DIRS" | head -n 1)"
fi
SOURCE_BYTES_OUTPUT=""
if ! SOURCE_BYTES_OUTPUT="$(du -sb "$WORLDS_DIR" 2>&1)"; then
  SOURCE_BYTES="0"
  fail_or_warn "could not measure source directory size with du -sb: $SOURCE_BYTES_OUTPUT"
else
  SOURCE_BYTES="$(awk '{print $1}' <<< "$SOURCE_BYTES_OUTPUT")"
fi
OUTPUT_PARENT="$(nearest_existing_parent "$OUTPUT_DIR")"
OUTPUT_FREE_BYTES="$(df -B1 --output=avail "$OUTPUT_PARENT" | tail -n 1 | tr -dc '0-9')"
if [[ -n "$SOURCE_BYTES_OUTPUT" && "$SOURCE_BYTES" != "0" ]]; then
  echo "Source directory size: $SOURCE_BYTES bytes ($(bytes_human "$SOURCE_BYTES"))"
else
  echo "Source directory size: UNKNOWN (du -sb failed)"
fi
echo "Output filesystem free: $OUTPUT_FREE_BYTES bytes ($(bytes_human "$OUTPUT_FREE_BYTES")) at $OUTPUT_PARENT"
if [[ -n "$SOURCE_BYTES_OUTPUT" && "$SOURCE_BYTES" != "0" ]] && (( OUTPUT_FREE_BYTES < SOURCE_BYTES )); then
  fail_or_warn "output filesystem has less free space than the source directory size."
fi

step "3. Validate symlinks"
verify_symlinks_inside_source "$WORLDS_DIR"

step "4. Build archive and checksum"
run_cmd "mkdir -p $(q "$OUTPUT_DIR")"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "+ write $MANIFEST_NAME with per-file sha256 entries into a temporary manifest"
  MANIFEST_FILE="/tmp/$MANIFEST_NAME"
else
  TMPDIR_CREATED="$(mktemp -d)"
  MANIFEST_FILE="$TMPDIR_CREATED/$MANIFEST_NAME"
  write_manifest "$WORLDS_DIR" "$MANIFEST_FILE" "$GENERATED_AT"
fi
run_cmd "tar -C $(q "$WORLDS_PARENT") --exclude=$(q "$WORLDS_BASE/$MANIFEST_NAME") --transform=$(q "$SOURCE_TRANSFORM") $TAR_CREATE_FLAGS $(q "$ARCHIVE_PATH") $(q "$WORLDS_BASE") -C $(q "$(dirname "$MANIFEST_FILE")") --transform=$(q "$MANIFEST_TRANSFORM") $(q "$MANIFEST_NAME")"
run_cmd "cd $(q "$OUTPUT_DIR") && sha256sum $(q "$ARCHIVE_BASENAME") > $(q "$SHA_BASENAME")"

step "5. Receiver commands"
cat <<EOF
scp $(q "$ARCHIVE_PATH") $(q "$SHA_PATH") px4@TARGET:/opt/databoss_px4_sim/
ssh px4@TARGET 'cd /opt/databoss_px4_sim && sha256sum -c $SHA_BASENAME'
ssh px4@TARGET 'cd /opt/databoss_px4_sim && tar $TAR_EXTRACT_FLAGS $ARCHIVE_BASENAME'
ssh px4@TARGET 'cd /opt/databoss_px4_sim && grep -E '"'"'^[0-9a-f]{64}  '"'"' generated_worlds/$MANIFEST_NAME | sha256sum -c -'
ssh px4@TARGET 'cd /opt/databoss_px4_sim && sudo chown -R px4:px4 generated_worlds'
EOF
