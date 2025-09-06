data "external" "github_repo" {
  program = ["bash", "${path.module}/get_repo.sh"]
}
