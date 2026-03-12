#!/usr/bin/env bash
# Docker health check — hits the /health endpoint.
curl -sf http://localhost:8000/health > /dev/null || exit 1
