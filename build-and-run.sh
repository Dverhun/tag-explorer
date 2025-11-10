#!/bin/bash

set -e  # Exit on error

# Configuration
IMAGE_NAME="aws-tag-controller"
IMAGE_TAG="v1.0"
CONTAINER_NAME="aws-tag-controller"
PORT="8000"
METADATA_DIR="./metadata"

echo "Building Docker image ${IMAGE_NAME}:${IMAGE_TAG}..."
docker build -t ${IMAGE_NAME}:${IMAGE_TAG} .

echo "Loading environment variables from metadata folder..."
ENV_ARGS=""

# Read all .sh files in metadata directory and source them
if [ -d "${METADATA_DIR}" ]; then
    for env_file in "${METADATA_DIR}"/*.sh; do
        if [ -f "${env_file}" ]; then
            echo "Loading variables from: ${env_file}"
            # Source the file to load variables into current shell
            set -a  # Automatically export all variables
            source "${env_file}"
            set +a
        fi
    done

    # Build environment arguments for docker run
    # Read variables from sourced files
    if [ ! -z "${AWS_ACCESS_KEY_ID}" ]; then
        ENV_ARGS="${ENV_ARGS} -e AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}"
    fi
    if [ ! -z "${AWS_SECRET_ACCESS_KEY}" ]; then
        ENV_ARGS="${ENV_ARGS} -e AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}"
    fi
    if [ ! -z "${AWS_SESSION_TOKEN}" ]; then
        ENV_ARGS="${ENV_ARGS} -e AWS_SESSION_TOKEN=${AWS_SESSION_TOKEN}"
    fi
    if [ ! -z "${AWS_DEFAULT_REGION}" ]; then
        ENV_ARGS="${ENV_ARGS} -e AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION}"
    fi
else
    echo "Warning: Metadata directory not found at ${METADATA_DIR}"
fi

echo "Checking for existing container..."
if [ "$(docker ps -aq -f name=^${CONTAINER_NAME}$)" ]; then
    echo "Stopping and removing existing container ${CONTAINER_NAME}..."
    docker stop ${CONTAINER_NAME}
    docker rm ${CONTAINER_NAME}
fi

echo "Starting new container ${CONTAINER_NAME} on port ${PORT}..."
# Use exec to replace the shell with docker run command
docker run -d \
    --name ${CONTAINER_NAME} \
    -p ${PORT}:8000 \
    ${ENV_ARGS} \
    ${IMAGE_NAME}:${IMAGE_TAG}

sleep 2
# Starting Logs Exporter
docker logs -f ${CONTAINER_NAME}
