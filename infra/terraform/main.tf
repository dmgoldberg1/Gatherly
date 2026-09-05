provider "yandex" {
  cloud_id  = var.cloud_id
  folder_id = var.folder_id
  zone      = var.zone
}
data "yandex_compute_image" "ubuntu" { family = "ubuntu-2404-lts" }
data "yandex_vpc_subnet" "existing" {
  count     = var.existing_subnet_id == "" ? 0 : 1
  subnet_id = var.existing_subnet_id
}
locals {
  network_id = var.existing_subnet_id == "" ? yandex_vpc_network.gatherly[0].id : data.yandex_vpc_subnet.existing[0].network_id
  subnet_id  = var.existing_subnet_id == "" ? yandex_vpc_subnet.gatherly[0].id : var.existing_subnet_id
}
resource "yandex_vpc_network" "gatherly" {
  count = var.existing_subnet_id == "" ? 1 : 0
  name  = "gatherly"
}
resource "yandex_vpc_subnet" "gatherly" {
  count          = var.existing_subnet_id == "" ? 1 : 0
  name           = "gatherly"
  zone           = var.zone
  network_id     = local.network_id
  v4_cidr_blocks = ["10.42.0.0/24"]
}
resource "yandex_vpc_address" "gatherly" {
  name = "gatherly-public"
  external_ipv4_address { zone_id = var.zone }
}
resource "yandex_vpc_security_group" "gatherly" {
  name       = "gatherly"
  network_id = local.network_id
  ingress {
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = [var.admin_cidr]
  }
  ingress {
    protocol       = "TCP"
    port           = 80
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    protocol       = "TCP"
    port           = 443
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "yandex_compute_instance" "gatherly" {
  name                      = "gatherly-demo"
  platform_id               = "standard-v3"
  zone                      = var.zone
  allow_stopping_for_update = true
  resources {
    cores         = 2
    core_fraction = 20
    memory        = 2
  }
  boot_disk {
    initialize_params {
      name     = "gatherly-data"
      image_id = data.yandex_compute_image.ubuntu.id
      type     = "network-hdd"
      size     = 20
    }
  }
  network_interface {
    subnet_id          = local.subnet_id
    nat                = true
    nat_ip_address     = yandex_vpc_address.gatherly.external_ipv4_address[0].address
    security_group_ids = [yandex_vpc_security_group.gatherly.id]
  }
  metadata = {
    ssh-keys  = "ubuntu:${trimspace(file(pathexpand(var.ssh_public_key_path)))}"
    user-data = "#cloud-config\nusers:\n  - name: ubuntu\n    groups: [sudo]\n    shell: /bin/bash\n    sudo: ALL=(ALL) NOPASSWD:ALL\n    ssh_authorized_keys:\n      - ${trimspace(file(pathexpand(var.ssh_public_key_path)))}\n"
  }
}
