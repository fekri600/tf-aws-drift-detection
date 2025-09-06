# modules/backend_setup/outputs.tf


output "region" {
  value = module.backend_setup.region
}

output "bucket_name" {
  value = module.backend_setup.bucket_name
}

output "trust_role_github" {
  description = "The ARN of the IAM role GitHub Actions can assume"
  value       = module.oidc.trust_role_github
}

output "debug_state_bucket_name" {
  value = module.oidc.debug_state_bucket_name
}
