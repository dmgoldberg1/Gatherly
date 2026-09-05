output "public_ip" { value = yandex_vpc_address.gatherly.external_ipv4_address[0].address }
output "instance_id" { value = yandex_compute_instance.gatherly.id }
output "app_url" { value = var.domain != "" ? "https://${var.domain}" : "http://${yandex_vpc_address.gatherly.external_ipv4_address[0].address}" }
output "ssh_command" { value = "ssh ubuntu@${yandex_vpc_address.gatherly.external_ipv4_address[0].address}" }
