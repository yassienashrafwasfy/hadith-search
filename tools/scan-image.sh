#!/usr/bin/env bash
# Scan a built image with trivy (CVEs) and dockle (Dockerfile/image best practices).
# Usage: tools/scan-image.sh [image]   (default: hadith-search:hardened)
# Both tools run from their official images, so nothing has to be installed locally.
# Build first: docker build --pull -t hadith-search:hardened .   (--pull picks up base-image patches)
set -euo pipefail

IMAGE="${1:-hadith-search:hardened}"
TRIVY_IMAGE="${TRIVY_IMAGE:-aquasec/trivy:0.69.3}"
DOCKLE_IMAGE="${DOCKLE_IMAGE:-goodwithtech/dockle:v0.4.15}"
CACHE_VOLUME="${TRIVY_CACHE_VOLUME:-trivy-cache}"
SOCK=/var/run/docker.sock

echo "== trivy: fixable HIGH/CRITICAL vulnerabilities in $IMAGE =="
# --scanners vuln skips trivy's secret scan, which chokes on the huge torch/scipy binaries
docker run --rm -v "$SOCK:$SOCK" -v "$CACHE_VOLUME:/root/.cache" "$TRIVY_IMAGE" image \
    --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 "$IMAGE"

echo "== dockle: image lint for $IMAGE =="
# -af settings.py: CIS-DI-0010 flags dill/scipy's settings.py as a credential file (false positive)
# -i DKL-DI-0005: the python:3.12-slim base image cleans apt with `apt-get dist-clean`, which
#                 dockle does not recognise; our own stages leave no apt lists behind
docker run --rm -v "$SOCK:$SOCK" "$DOCKLE_IMAGE" \
    -af settings.py -i DKL-DI-0005 --exit-code 1 --exit-level warn "$IMAGE"
