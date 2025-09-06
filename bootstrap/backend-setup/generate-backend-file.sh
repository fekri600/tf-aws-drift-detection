#!/usr/bin/env bash
set -e
BUCKET_NAME="$1"
REGION="$2"
# Generate the backend.tf file in the bootstrap-improve root directory
cat > "../backend.tf" <<EOF
terraform {
  backend "s3" {
    bucket         = "${BUCKET_NAME}"
    key            = "envs/terraform.tfstate"
    region         = "${REGION}"
    use_lockfile   = true
    encrypt        = true
  }
}
EOF


