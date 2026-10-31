#!/usr/bin/env bash
# Provide the two inputs CI cannot derive on its own.
#
# Prepared for the workflow's `pre_build` input:
#
#     bash scripts/ci-prepare-inputs.sh
#
# Both inputs are published as release assets of this repository, which is what
# README.md documents: a fork's GITHUB_TOKEN cannot read another repository's
# workflow artifacts, but it can read a release asset of its own repository.
#
#   fuxi-pmgr-baseline/kernel.macho  the extracted original kernel the PMGR, UFS
#                                    and platform-power builders pin by sha256
#   fuxi-owner-policy/root-policy.tgz  the signed owner-root policy bundle the
#                                    verified-root builder consumes
#
# The variables land in $GITHUB_ENV so later steps (and the build itself) see
# them.
set -euo pipefail

repository="${FUXI_RELEASE_REPO:-Kerfand/UniXNU-fuxi}"
work="${RUNNER_TEMP:?RUNNER_TEMP is required}"

mkdir -p "$work/fuxi-inputs"
cd "$work/fuxi-inputs"

# The assets live in the device repository, which is public; a workflow token
# from the framework fork can read public release assets.
if [ -n "${GITHUB_TOKEN:-}" ]; then export GH_TOKEN="$GITHUB_TOKEN"; fi

echo "== downloading the pinned reference kernel"
gh release download fuxi-pmgr-baseline -R "$repository" -p kernel.macho -D . --clobber

echo "== downloading the owner root policy bundle"
gh release download fuxi-owner-policy -R "$repository" -p root-policy.tgz -D . --clobber
mkdir -p root-policy
tar xzf root-policy.tgz -C root-policy --strip-components=1

kernel="$PWD/kernel.macho"
policy="$PWD/root-policy"

test -f "$kernel" || { echo "missing kernel.macho" >&2; exit 1; }
test -d "$policy" || { echo "missing root-policy" >&2; exit 1; }

echo "reference kernel: $kernel ($(wc -c < "$kernel") bytes)"
echo "root policy: $policy ($(find "$policy" -type f | wc -l) files)"

{
  echo "FUXI_PMGR_REFERENCE_KERNEL=$kernel"
  echo "FUXI_ROOT_POLICY=$policy"
} >> "$GITHUB_ENV"
