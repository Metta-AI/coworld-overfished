#!/usr/bin/env bash
# Coworld build hook: recreate the static replay viewer bundle from viewer/ sources.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${1:?usage: tools/build_replay_viewer.sh /absolute/output/dir}"
if [[ "${output_dir}" != /* && ! "${output_dir}" =~ ^[A-Za-z]:[\\/] ]] \
  || [[ "${output_dir}" == "/" || "${output_dir}" == "${repo_dir}" ]]; then
  echo "unsafe bundle output: ${output_dir}" >&2
  exit 1
fi

rm -rf "${output_dir}"
mkdir -p "${output_dir}"
PYTHONPATH="${repo_dir}/src" python3 - "${repo_dir}/viewer" "${output_dir}" <<'PY'
import sys
from pathlib import Path
from overfished.viewer_build import write_bundle
target = write_bundle(Path(sys.argv[1]), Path(sys.argv[2]))
print(f"wrote {target} ({target.stat().st_size} bytes)")
PY
