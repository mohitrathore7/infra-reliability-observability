variable "project" {
  description = "Name prefix applied to every resource."
  type        = string
  default     = "infra-reliability"
}

variable "region" {
  description = "AWS region for the cluster."
  type        = string
  default     = "eu-central-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the cluster VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR block for the public subnet that hosts all nodes."
  type        = string
  default     = "10.20.1.0/24"
}

variable "admin_cidr" {
  description = "CIDR allowed to reach SSH, the Kubernetes API and Grafana. Set this to your own IP/32."
  type        = string

  validation {
    condition     = can(cidrhost(var.admin_cidr, 0))
    error_message = "admin_cidr must be a valid CIDR, e.g. 203.0.113.10/32."
  }
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key installed on every node."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "control_plane_instance_type" {
  description = "Instance type for the Kubernetes control-plane node."
  type        = string
  default     = "t3.large"
}

variable "worker_instance_type" {
  description = "Instance type for worker nodes (Spark, Kafka, Trino run here)."
  type        = string
  default     = "t3.xlarge"
}

variable "worker_count" {
  description = "Number of worker nodes. 3 workers + 1 control plane = 4-node cluster."
  type        = number
  default     = 3

  validation {
    condition     = var.worker_count >= 3
    error_message = "At least 3 workers are needed so Kafka keeps a quorum when one node fails."
  }
}

variable "root_volume_gb" {
  description = "Root EBS volume size per node, in GB."
  type        = number
  default     = 60
}
