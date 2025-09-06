# modules/backend_setup/outputs.tf
data "aws_region" "current" {}

output "region" {
  value = data.aws_region.current.id
}

output "bucket_name" {
  value = aws_s3_bucket.terraform_state.id
}


