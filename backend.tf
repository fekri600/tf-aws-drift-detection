terraform {
  backend "s3" {
    bucket         = "tf-aws-drift-detection-state-1d0d507b"
    key            = "envs/terraform.tfstate"
    region         = "us-east-1"
    use_lockfile   = true
    encrypt        = true
  }
}
