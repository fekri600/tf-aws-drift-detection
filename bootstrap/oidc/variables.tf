variable "oidc_url" {
  description = "OIDC provider URL"
  type        = string
}

variable "oidc_client_id_list" {
  description = "OIDC client IDs"
  type        = list(string)
}

variable "oidc_thumbprint_list" {
  description = "OIDC thumbprint list"
  type        = list(string)
}

variable "iam_role_name" {
  description = "IAM role name"
  type        = string
}

variable "iam_policy_name" {
  description = "IAM policy name"
  type        = string
}

variable "state_bucket_name" {
  description = "The name of the S3 state bucket passed from backend_setup module"
  type        = string
}

variable "branch" {
  description = "The branch of the repository to be used"
  type        = string
  default = "main"
}

variable "repo_owner" {
  description = "The owner of the repository to be used"
  type        = string
}

variable "repo_name" {
  description = "The name of the repository to be used"
  type        = string
}