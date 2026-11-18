output "control_plane_public_ip" {
  description = "Public IP of the control-plane node."
  value       = aws_instance.control_plane.public_ip
}

output "worker_public_ips" {
  description = "Public IPs of the worker nodes."
  value       = aws_instance.worker[*].public_ip
}

output "node_private_ips" {
  description = "Private IPs keyed by node name (used by the chaos runner)."
  value = merge(
    { (aws_instance.control_plane.tags.Name) = aws_instance.control_plane.private_ip },
    { for w in aws_instance.worker : w.tags.Name => w.private_ip }
  )
}

output "ansible_inventory_path" {
  description = "Generated Ansible inventory."
  value       = local_file.ansible_inventory.filename
}
