resource "aws_s3_bucket" "demo" {
  bucket = "drift-demo-bucket-${random_id.suffix.hex}"
  force_destroy = true

  tags = {
    Project = "Terraform Drift Demo"
  }

  tags = {
  }
}

resource "random_id" "suffix" {
  byte_length = 2
}
