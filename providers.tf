provider "aws" {
  region  = "us-east-1"
  alias   = "primary"
  profile = "default"
}

provider "aws" {
  region = "us-west-1"
  alias  = "secondary"
}