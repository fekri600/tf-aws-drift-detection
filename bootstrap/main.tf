# Get current account information

module "backend_setup" {
  source              = "./backend-setup"
  state_bucket_name   = "${module.repo-param.repo_name}-state-${random_id.bucket_suffix.hex}"
}



module "oidc" {
  source = "./oidc"

  # Pass backend_setup output as input
  state_bucket_name = module.backend_setup.bucket_name

  # Pass remaining variables from bootstrap
  iam_role_name        = var.iam_role_name
  iam_policy_name      = var.iam_policy_name
  oidc_url             = var.oidc_url
  oidc_client_id_list  = var.oidc_client_id_list
  oidc_thumbprint_list = var.oidc_thumbprint_list
  branch               = "main"
  repo_owner           = module.repo-param.repo_owner
  repo_name            = module.repo-param.repo_name
}

module "repo-param" {
  source = "./repo-param"
}





