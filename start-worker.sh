#!/bin/bash
# Startup script for the worker service on Railway.

set -e

echo "Starting Sandwich Co-Pilot Worker..."
echo "Environment: $ENVIRONMENT"

# Wait a moment for the API service to finish DB init
sleep 5

exec python -m app.worker
