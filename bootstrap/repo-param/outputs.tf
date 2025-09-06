output "repo_name" {
  value = data.external.github_repo.result.name
}

output "repo_owner" {
  value = data.external.github_repo.result.owner
}