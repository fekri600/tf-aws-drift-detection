




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
  description = "Name for the IAM role used by GitHub Actions"
  type        = string
}

variable "iam_policy_name" {
  description = "Name for the IAM policy attached to the GitHub Actions role"
  type        = string
}
