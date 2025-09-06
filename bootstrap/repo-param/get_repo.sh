#!/usr/bin/env bash
set -e

# Query GitHub for repo slug
REPO_SLUG=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
OWNER="${REPO_SLUG%/*}"
NAME="${REPO_SLUG#*/}"

# Output in JSON (Terraform external provider expects JSON)
cat <<EOF
{
  "owner": "$OWNER",
  "name": "$NAME"
}
EOF
