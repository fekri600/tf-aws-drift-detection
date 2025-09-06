terraform {
  required_version = "v1.12.2"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.7.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.6.3"
    }
  }

}

provider "aws" {
  region = "us-east-1"
}
