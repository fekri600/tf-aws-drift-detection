terraform {
  required_version = "v1.12.2"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.7.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
  }
}