.PHONY: help infra cluster stack dashboard chaos-list chaos-dry chaos-all test lint destroy

PROM ?= http://$(shell cd terraform && terraform output -raw control_plane_public_ip 2>/dev/null):30090
export KUBECONFIG := $(CURDIR)/kubeconfig

help:          ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

infra:         ## Provision 4 EC2 nodes + network with Terraform
	cd terraform && terraform init && terraform apply

cluster:       ## Configure Kubernetes on the nodes with Ansible
	cd ansible && ansible-galaxy collection install -r requirements.yml && ansible-playbook site.yml

stack:         ## Deploy Prometheus/Grafana, Kafka, Spark, Trino
	./scripts/deploy-stack.sh

dashboard:     ## Regenerate the Grafana dashboard JSON
	python3 scripts/build_dashboard.py > monitoring/dashboards/cluster-reliability.json

chaos-list:    ## List failure scenarios
	cd chaos && python3 -m chaoskit list

chaos-dry:     ## Print every inject/revert command without running anything
	cd chaos && python3 -m chaoskit run --all --dry-run

chaos-all:     ## Run all 12 scenarios against the live cluster
	cd chaos && python3 -m chaoskit run --all --prometheus $(PROM)

test:          ## Unit tests for the chaos toolkit
	cd chaos && python3 -m pytest -q

lint:          ## Lint YAML, Python, Terraform
	yamllint -c .yamllint.yml k8s monitoring chaos/scenarios.yaml ansible
	cd chaos && ruff check .
	cd terraform && terraform fmt -check -recursive

destroy:       ## Tear everything down
	cd terraform && terraform destroy
