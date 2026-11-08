#!/usr/bin/env bash
# Build the MCP server image and export it as a tar archive for offline use.
#
# The Dockerfile's runtime stage is Alpine, so the exported image is small and
# self-contained. Single-platform only: `docker save` cannot represent a
# multi-arch manifest, so use -p to pick the architecture you need.
#
#   scripts/export_image.sh                    # host arch -> dist/decision-mcp-local.tar
#   scripts/export_image.sh -t v0.1.0 --gzip   # -> dist/decision-mcp-v0.1.0.tar.gz
#   scripts/export_image.sh -p linux/arm64     # cross-build for arm64 (needs QEMU)
#
# On the target machine:
#
#   docker load -i decision-mcp-local.tar
#   docker run --rm -p 8080:8080 -e DECISION_API_KEY=... -e DECISION_MODEL=... decision-mcp:local
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

IMAGE="${IMAGE:-decision-mcp}"
TAG=local
OUTPUT=
PLATFORM=
GZIP=0
BUILD=1
CHECKSUM=1

usage() {
    sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    cat <<'EOF'

Options:
  -t, --tag TAG         image tag (default: local)
  -o, --output FILE     archive path (default: dist/<image>-<tag>.tar)
  -p, --platform P      target platform, e.g. linux/amd64 (default: host)
      --gzip            compress the archive (.tar.gz)
      --no-build        export an existing image instead of building
      --no-checksum     do not write a .sha256 file
  -h, --help            show this help

Environment:
  IMAGE                 image name (default: decision-mcp)
  APK_MIRROR            apk mirror host passed to the build, e.g.
                        https://mirrors.aliyun.com (default: Alpine CDN)
  DOCKER_BUILD_NETWORK  build-time network, e.g. host, when the container
                        network cannot resolve external hosts
EOF
}

die() {
    printf 'error: %s\n' "$1" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        -t | --tag)
            TAG="${2:?--tag requires a value}"
            shift 2
            ;;
        -o | --output)
            OUTPUT="${2:?--output requires a value}"
            shift 2
            ;;
        -p | --platform)
            PLATFORM="${2:?--platform requires a value}"
            shift 2
            ;;
        --gzip)
            GZIP=1
            shift
            ;;
        --no-build)
            BUILD=0
            shift
            ;;
        --no-checksum)
            CHECKSUM=0
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1 (try --help)"
            ;;
    esac
done

command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH"
docker info >/dev/null 2>&1 || die "cannot talk to the Docker daemon"

[ -n "$TAG" ] || die "--tag must not be empty"
REF="$IMAGE:$TAG"

# Colons and slashes are legal in tags but not in filenames.
SAFE_TAG="${TAG//[:\/]/-}"
OUTPUT="${OUTPUT:-$ROOT/dist/${IMAGE//\//-}-$SAFE_TAG.tar}"
if [ "$GZIP" -eq 1 ] && [ "${OUTPUT%.gz}" = "$OUTPUT" ]; then
    OUTPUT="$OUTPUT.gz"
fi
mkdir -p "$(dirname "$OUTPUT")"

if [ "$BUILD" -eq 1 ]; then
    BUILD_ARGS=()
    if [ -n "${APK_MIRROR:-}" ]; then
        printf '==> apk mirror: %s\n' "$APK_MIRROR" >&2
        BUILD_ARGS+=(--build-arg "APK_MIRROR=${APK_MIRROR%/}")
    fi
    if [ -n "${DOCKER_BUILD_NETWORK:-}" ]; then
        printf '==> build network: %s\n' "$DOCKER_BUILD_NETWORK" >&2
        BUILD_ARGS+=(--network "$DOCKER_BUILD_NETWORK")
    fi
    if [ -n "$PLATFORM" ]; then
        printf '==> building %s for %s\n' "$REF" "$PLATFORM" >&2
        docker buildx version >/dev/null 2>&1 || die "--platform requires docker buildx"
        docker buildx build --platform "$PLATFORM" --load -t "$REF" \
            ${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"} "$ROOT"
    else
        printf '==> building %s\n' "$REF" >&2
        docker build -t "$REF" ${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"} "$ROOT"
    fi
else
    docker image inspect "$REF" >/dev/null 2>&1 ||
        die "image $REF not found; run without --no-build first"
fi

printf '\n==> exporting %s -> %s\n' "$REF" "$OUTPUT" >&2
if [ "$GZIP" -eq 1 ]; then
    docker save "$REF" | gzip >"$OUTPUT"
else
    docker save -o "$OUTPUT" "$REF"
fi

if [ "$CHECKSUM" -eq 1 ]; then
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$OUTPUT" >"$OUTPUT.sha256"
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$OUTPUT" >"$OUTPUT.sha256"
    else
        printf 'warning: no sha256sum/shasum found, skipping checksum\n' >&2
    fi
    if [ -f "$OUTPUT.sha256" ]; then
        printf '==> wrote %s\n' "$OUTPUT.sha256" >&2
    fi
fi

printf '\nLoad on the target machine with:\n  docker load -i %s\n' "$OUTPUT"
