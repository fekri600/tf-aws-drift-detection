resource "aws_s3_bucket" "demo" {
  bucket = "drift-demo-bucket-${random_id.suffix.hex}"
  force_destroy = true



  tags = {
    test = "just for test"
  }
}

resource "random_id" "suffix" {
  byte_length = 2
}
