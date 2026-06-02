#!/bin/bash
set -e

# Generate version.txt: [git revision] Build: [timestamp]
GIT_REV=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S %Z")
VERSION_STR="${GIT_REV} Build: ${TIMESTAMP}"

echo "Generating version.txt with content: ${VERSION_STR}"
echo "${VERSION_STR}" > version.txt

# Registry details
REGISTRY="registry.shifamily.com"
REPOSITORY="homestack/gws-mcp"
IMAGE_TAG_LATEST="${REGISTRY}/${REPOSITORY}:latest"
IMAGE_TAG_REV="${REGISTRY}/${REPOSITORY}:${GIT_REV}"

echo "Building docker image..."
docker build -t "${IMAGE_TAG_LATEST}" -t "${IMAGE_TAG_REV}" .

echo "Pushing docker image tags..."
echo "Pushing ${IMAGE_TAG_LATEST}..."
docker push "${IMAGE_TAG_LATEST}"

echo "Pushing ${IMAGE_TAG_REV}..."
docker push "${IMAGE_TAG_REV}"

echo "Docker build and push completed successfully!"
