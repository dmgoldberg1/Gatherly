terraform {
  required_version = "~> 1.16.0"
  required_providers {
    yandex = { source = "yandex-cloud/yandex", version = "~> 0.220.0" }
  }
}

provider "yandex" {
  cloud_id  = var.cloud_id
  folder_id = var.folder_id
  zone      = var.zone
}

variable "cloud_id" { type = string }
variable "folder_id" { type = string }
variable "zone" {
  type    = string
  default = "ru-central1-a"
}
variable "load_zone" {
  type    = string
  default = "ru-central1-d"
}
variable "layout" {
  type    = string
  default = "distributed"
  validation {
    condition     = contains(["compact", "distributed"], var.layout)
    error_message = "layout must be compact or distributed."
  }
}
variable "preemptible" {
  type        = bool
  default     = true
  description = "Use preemptible VMs for the temporary load benchmark."
}
variable "stand_name" {
  type    = string
  default = "gatherly-bench"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.stand_name))
    error_message = "stand_name must be a short lowercase resource name."
  }
}
variable "replicas" {
  type    = number
  default = 3
  validation {
    condition     = var.replicas >= 1 && var.replicas <= 10 && floor(var.replicas) == var.replicas
    error_message = "replicas must be an integer in 1..10."
  }
}
variable "ssh_public_key_path" { type = string }
variable "admin_cidr" {
  type = string
  validation {
    condition     = can(cidrhost(var.admin_cidr, 0)) && endswith(var.admin_cidr, "/32")
    error_message = "Restrict SSH and stand access to an administrator IPv4 /32."
  }
}
variable "bucket_name" { type = string }
variable "existing_subnet_id" {
  type    = string
  default = ""
}

data "yandex_compute_image" "ubuntu" { family = "ubuntu-2404-lts" }
data "yandex_vpc_subnet" "existing" {
  count     = var.existing_subnet_id == "" ? 0 : 1
  subnet_id = var.existing_subnet_id
}
resource "yandex_vpc_network" "bench" {
  count = var.existing_subnet_id == "" ? 1 : 0
  name  = var.stand_name
}
resource "yandex_vpc_subnet" "bench" {
  count          = var.existing_subnet_id == "" ? 1 : 0
  name           = var.stand_name
  zone           = var.load_zone
  network_id     = yandex_vpc_network.bench[0].id
  v4_cidr_blocks = ["10.52.0.0/24"]
}
resource "yandex_vpc_subnet" "compact" {
  count          = 1
  name           = "${var.stand_name}-server"
  zone           = var.zone
  network_id     = local.network_id
  v4_cidr_blocks = ["10.53.0.0/24"]
}
locals {
  subnet_id  = var.existing_subnet_id == "" ? yandex_vpc_subnet.bench[0].id : var.existing_subnet_id
  network_id = var.existing_subnet_id == "" ? yandex_vpc_network.bench[0].id : data.yandex_vpc_subnet.existing[0].network_id
  hosts = var.layout == "compact" ? { server = "compact", load = "load" } : merge({ proxy = "proxy", database = "database", monitoring = "monitoring", load = "load" }, {
    for index in range(var.replicas) : format("app-%03d", index + 1) => "app"
  })
}
resource "yandex_vpc_security_group" "bench" {
  name       = var.stand_name
  network_id = local.network_id
  ingress {
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = [var.admin_cidr]
  }
  ingress {
    protocol       = "TCP"
    port           = 8080
    v4_cidr_blocks = [var.admin_cidr]
  }
  ingress {
    protocol          = "ANY"
    predefined_target = "self_security_group"
  }
  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "yandex_compute_instance" "host" {
  for_each                  = local.hosts
  name                      = "${var.stand_name}-${each.key}"
  platform_id               = "standard-v3"
  zone                      = each.value == "load" ? var.load_zone : var.zone
  allow_stopping_for_update = true
  scheduling_policy {
    preemptible = var.preemptible
  }
  resources {
    cores         = 8
    core_fraction = 100
    memory        = 16
  }
  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      type     = "network-ssd"
      size     = 20
    }
  }
  network_interface {
    subnet_id          = each.value == "load" ? local.subnet_id : yandex_vpc_subnet.compact[0].id
    nat                = true
    security_group_ids = [yandex_vpc_security_group.bench.id]
  }
  metadata = {
    ssh-keys  = "ubuntu:${trimspace(file(pathexpand(var.ssh_public_key_path)))}"
    user-data = "#cloud-config\nusers:\n  - name: ubuntu\n    groups: [sudo]\n    shell: /bin/bash\n    sudo: ALL=(ALL) NOPASSWD:ALL\n    ssh_authorized_keys:\n      - ${trimspace(file(pathexpand(var.ssh_public_key_path)))}\n"
  }
  lifecycle { ignore_changes = [boot_disk[0].initialize_params[0].image_id] }
  labels = { project = "gatherly", purpose = "load-bench", role = each.value }
}
resource "yandex_iam_service_account" "objects" {
  name = "${var.stand_name}-objects"
}
resource "yandex_storage_bucket" "objects" {
  bucket    = var.bucket_name
  folder_id = var.folder_id
  grant {
    id          = yandex_iam_service_account.objects.id
    type        = "CanonicalUser"
    permissions = ["READ", "WRITE"]
  }

}
resource "yandex_iam_service_account_static_access_key" "objects" {
  service_account_id = yandex_iam_service_account.objects.id
  description        = "Private Gatherly benchmark object access"
}
output "hosts" {
  value = { for name, host in yandex_compute_instance.host : name => {
    id         = host.id
    public_ip  = host.network_interface[0].nat_ip_address
    private_ip = host.network_interface[0].ip_address
    role       = local.hosts[name]
  } }
}
output "layout" { value = var.layout }
output "bucket" { value = yandex_storage_bucket.objects.bucket }
output "object_access_key" {
  value     = yandex_iam_service_account_static_access_key.objects.access_key
  sensitive = true
}
output "object_secret_key" {
  value     = yandex_iam_service_account_static_access_key.objects.secret_key
  sensitive = true
}
