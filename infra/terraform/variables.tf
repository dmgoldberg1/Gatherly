variable "cloud_id" { type = string }
variable "folder_id" { type = string }
variable "zone" {
  type    = string
  default = "ru-central1-d"
}
variable "ssh_public_key_path" { type = string }
variable "admin_cidr" {
  type = string
  validation {
    condition     = can(cidrhost(var.admin_cidr, 0)) && endswith(var.admin_cidr, "/32")
    error_message = "Use your current public IPv4 address with /32 for SSH."
  }
}
variable "domain" {
  type    = string
  default = ""
}

variable "existing_subnet_id" {
  type        = string
  default     = ""
  description = "Optional existing subnet in the chosen zone, useful when the VPC quota is exhausted."
}
