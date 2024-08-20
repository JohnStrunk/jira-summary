#! /bin/bash

set -e -o pipefail

SECRET_NAME="jira-summarizer-secret"  # pragma: allowlist secret

# We should have a single argument that is the name of the namespace
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <namespace>"
    exit 1
fi
NAMESPACE="$1"

# Make sure the .env file exists
if [ ! -f .env ]; then
    echo "The .env file does not exist"
    exit 1
fi

set -x
kubectl -n "$NAMESPACE" delete secret "$SECRET_NAME" || true
kubectl -n "$NAMESPACE" create secret generic "$SECRET_NAME" --from-env-file=.env
set +x
