# AWS Tag Compliance Metrics Exporter

**Prometheus metrics exporter for AWS resource tag compliance across multiple accounts and regions.**

Scans AWS resources using the Resource Groups Tagging API, validates required tags, and exports compliance metrics in Prometheus format for monitoring and alerting.

## Core Function

**Single Purpose**: Export Prometheus-compatible metrics for AWS resource tag compliance

- Discovers all tagged AWS resources across accounts/regions via cross-account role assumption
- Validates presence of required tags defined in configuration
- Exports detailed Prometheus metrics with labels for account, region, tag, resource type, and ARN
- Outputs metrics to stdout or file for integration with monitoring systems

## Quick Start

### CLI Mode (One-time Scan)

```bash
# Install dependencies
pip install -r requirements.txt

# Run scan and output metrics to stdout
python main.py

# Export metrics to file
python main.py --output metrics.txt

# Use custom config
python main.py --config custom-config.yaml
```

### Web Mode (Long-running Service)

Run as a web server that periodically scans AWS resources and exposes metrics on HTTP endpoint:

```bash
# Start web server (default: http://0.0.0.0:8080)
python main.py --web

# Custom port and refresh interval
python main.py --web --port 9090 --refresh-interval 600

# With custom config
python main.py --web --config custom-config.yaml --refresh-interval 300
```

**Web Mode Endpoints:**
- `GET /metrics` - Prometheus metrics (for scraping)
- `GET /health` - Health check (Kubernetes liveness probe)
- `GET /ready` - Readiness check (Kubernetes readiness probe)
- `GET /` - Service status and information

Web mode is designed for Kubernetes deployment where Prometheus scrapes the `/metrics` endpoint.

## Configuration

Configuration file: `config.yaml`

```yaml
# Accounts and regions to scan
aws_account_matrix:
  - account_id: "123456789012"
    account_name: "production"
    regions: ["us-east-1", "eu-west-1"]

  - account_id: "210987654321"
    account_name: "staging"
    regions: ["us-east-1"]

# IAM role template for cross-account access
# Uses {account_id} placeholder
assume_role_name_template: "terraform"

# Required tags for compliance
REQUIRED_TAGS:
  - environment
  - product
  - owner
  - cost_center
```

### Optional: Account-Specific Role Overrides

```yaml
aws_account_overrides:
  "123456789012":
    role_arn: "arn:aws:iam::123456789012:role/CustomAuditRole"
```

### Optional: Exclude Resource Types

Exclude specific resource types from scanning (e.g., Kubernetes pods, ECS tasks):

```yaml
excluded_resource_types:
  - "pod"           # Excludes resources with "pod" in type
  - "ecs:task"      # Excludes ECS tasks
  - "eks:*"         # Excludes all EKS resources (wildcard)
  - "container"     # Excludes container resources
```

Patterns support:
- **Substring match**: Pattern contained in resource type (case-insensitive)
- **Wildcard**: Use `*` for prefix matching (e.g., `"eks:*"` matches all EKS types)
- **Service-specific**: Format as `"service:type"` for precision

## Prometheus Metrics

### Exported Metrics

#### Basic Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `tag_compliant_total` | Gauge | Resources with required tag present | tag, account_name, account_id, region |
| `tag_non_compliant_total` | Gauge | Resources missing required tag | tag, account_name, account_id, region |
| `tag_missing_detail` | Gauge | Per-resource missing tag indicator (1) | tag, account_name, account_id, region, resource_type, resource_arn |
| `resources_scanned_total` | Gauge | Total resources scanned | account_name, account_id, region |
| `compliance_percentage` | Gauge | Overall compliance percentage | account_name, account_id, region |

#### Advanced Compliance Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `tag_compliance_percentage` | Gauge | Compliance % for each individual tag | tag, account_name, account_id, region |
| `tag_resource_type_compliance_percentage` | Gauge | Compliance % per tag and resource type | tag, resource_type, account_name, account_id, region |
| `resources_fully_compliant_total` | Gauge | Resources with ALL required tags | account_name, account_id, region |
| `resources_fully_compliant_by_type_total` | Gauge | Fully compliant resources by type | resource_type, account_name, account_id, region |

### Example Metrics Output

```prometheus
# HELP tag_compliant_total Resources compliant with required tag
# TYPE tag_compliant_total gauge
tag_compliant_total{account_id="123456789012",account_name="production",region="us-east-1",tag="environment"} 245.0

# HELP tag_non_compliant_total Resources missing required tag
# TYPE tag_non_compliant_total gauge
tag_non_compliant_total{account_id="123456789012",account_name="production",region="us-east-1",tag="owner"} 12.0

# HELP tag_missing_detail Detailed missing tag indicator (1 per resource/tag combination)
# TYPE tag_missing_detail gauge
tag_missing_detail{account_id="123456789012",account_name="production",region="us-east-1",resource_arn="arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0",resource_type="instance",tag="owner"} 1.0

# HELP resources_scanned_total Total resources scanned
# TYPE resources_scanned_total gauge
resources_scanned_total{account_id="123456789012",account_name="production",region="us-east-1"} 257.0

# HELP compliance_percentage Overall tag compliance percentage
# TYPE compliance_percentage gauge
compliance_percentage{account_id="123456789012",account_name="production",region="us-east-1"} 95.3

# HELP tag_compliance_percentage Compliance percentage per individual tag
# TYPE tag_compliance_percentage gauge
tag_compliance_percentage{account_id="123456789012",account_name="production",region="us-east-1",tag="environment"} 98.5
tag_compliance_percentage{account_id="123456789012",account_name="production",region="us-east-1",tag="owner"} 92.1

# HELP tag_resource_type_compliance_percentage Compliance percentage per tag and resource type
# TYPE tag_resource_type_compliance_percentage gauge
tag_resource_type_compliance_percentage{account_id="123456789012",account_name="production",region="us-east-1",resource_type="instance",tag="environment"} 100.0
tag_resource_type_compliance_percentage{account_id="123456789012",account_name="production",region="us-east-1",resource_type="bucket",tag="environment"} 95.0

# HELP resources_fully_compliant_total Resources with all required tags present
# TYPE resources_fully_compliant_total gauge
resources_fully_compliant_total{account_id="123456789012",account_name="production",region="us-east-1"} 245.0

# HELP resources_fully_compliant_by_type_total Fully compliant resources grouped by resource type
# TYPE resources_fully_compliant_by_type_total gauge
resources_fully_compliant_by_type_total{account_id="123456789012",account_name="production",region="us-east-1",resource_type="instance"} 120.0
resources_fully_compliant_by_type_total{account_id="123456789012",account_name="production",region="us-east-1",resource_type="bucket"} 85.0
```

## AWS Permissions

### Base IAM Permissions

The credentials running the scan require:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "tag:GetResources",
        "sts:AssumeRole"
      ],
      "Resource": "*"
    }
  ]
}
```

### Cross-Account IAM Role

In each target account, create a role with:

**Trust Relationship:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::<base-account-id>:root"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

**Permissions:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "tag:GetResources",
        "tag:GetTagKeys",
        "tag:GetTagValues"
      ],
      "Resource": "*"
    }
  ]
}
```

## Docker Deployment

```bash
# Build image
docker build -t aws-tag-exporter .

# Web Mode (default) - Run as HTTP server
docker run -d \
  -p 8080:8080 \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd)/config.yaml:/app/config.yaml \
  --name aws-tag-exporter \
  aws-tag-exporter

# Test metrics endpoint
curl http://localhost:8080/metrics

# CLI Mode - One-time scan
docker run --rm \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd)/config.yaml:/app/config.yaml \
  aws-tag-exporter \
  python main.py --output /app/metrics.txt

# CLI Mode - Output to stdout
docker run --rm \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd)/config.yaml:/app/config.yaml \
  aws-tag-exporter \
  python main.py
```

## Kubernetes Deployment

Deploy as a long-running service in Kubernetes with automatic Prometheus scraping:

```bash
# See k8s/ directory for full manifests

# Quick deploy with kubectl
kubectl apply -f k8s/serviceaccount.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# Or use Kustomize
kubectl apply -k k8s/

# Verify deployment
kubectl port-forward svc/aws-tag-exporter 8080:8080
curl http://localhost:8080/metrics
```

**Features:**
- Runs as Deployment with automatic restarts
- Periodic background scanning (configurable interval)
- Health and readiness probes for Kubernetes
- Prometheus scraping via Service annotations or ServiceMonitor
- Support for AWS IRSA (IAM Roles for Service Accounts)

See [k8s/README.md](k8s/README.md) for detailed deployment guide.

## Integration with Prometheus

### Method 1: Web Mode + HTTP Service Discovery (Recommended)

Run in web mode and let Prometheus scrape the `/metrics` endpoint:

```yaml
# Prometheus scrape config
scrape_configs:
  - job_name: 'aws-tag-exporter'
    static_configs:
      - targets: ['localhost:8080']  # Or service address in Kubernetes
    scrape_interval: 60s
```

In Kubernetes, use Service annotations for automatic discovery:
```yaml
annotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "8080"
  prometheus.io/path: "/metrics"
```

### Method 2: Prometheus Node Exporter Textfile Collector

Export metrics to file for collection (CLI mode):

```bash
# Run periodically (e.g., via cron)
python main.py --output /var/lib/node_exporter/textfile_collector/aws_tags.prom

# Cron example (hourly)
0 * * * * cd /app && python main.py --output /var/lib/node_exporter/textfile_collector/aws_tags.prom
```

### Method 3: Pushgateway

Push metrics to Prometheus Pushgateway (CLI mode):

```bash
python main.py --output metrics.txt
curl --data-binary @metrics.txt http://pushgateway:9091/metrics/job/aws_tag_compliance
```

## Architecture

### Components

```
main.py
   Orchestration: CLI argument parsing, config loading
   Calls: scan_and_export_metrics()

src/aws_audit.py
   Cross-account credential management (STS AssumeRole)
   Resource discovery (Resource Groups Tagging API)
   Tag compliance validation

src/metrics.py
   Prometheus metric definitions (Gauges)
   Metric update logic from scan results
   Metric export (generate_latest)
```

### Data Flow

```
config.yaml � Load config
    �
AWS STS � Assume cross-account roles
    �
Resource Groups Tagging API � Discover resources (paginated)
    �
Tag validation � Compare against required_tags
    �
Prometheus metrics � Update gauges with labels
    �
Output � stdout or file
```

### Resource Discovery

Uses **AWS Resource Groups Tagging API** for cross-service discovery:
- Single API for all resource types (EC2, S3, RDS, Lambda, etc.)
- Pagination support for large resource sets
- **Limitation**: Only discovers resources with at least one tag

### Cross-Account Pattern

**STS AssumeRole** for multi-account scanning:
1. Base credentials from environment/profile
2. For each account: construct role ARN from template or override
3. Assume role � get temporary credentials (1 hour session)
4. Use temporary credentials for Resource Groups Tagging API client
5. Scan all configured regions

## Error Handling

- **Account-level errors**: Logged and skipped; other accounts continue
- **Region-level errors**: Logged and recorded in results; other regions continue
- **Accounts with errors**: Excluded from metrics export

## Limitations

1. **Untagged resources**: Resources with zero tags are invisible to Resource Groups Tagging API
2. **Cardinality**: ARN labels truncated to 200 chars to prevent metric explosion
3. **Rate limiting**: AWS API throttling may slow large scans (automatic retries configured)

## Web Mode Operations

### Background Scanning

Web mode runs a background task that periodically scans AWS resources:

- **Default interval**: 300 seconds (5 minutes)
- **Configurable**: `--refresh-interval` flag or environment variable
- **First scan**: Runs immediately on startup
- **In-memory metrics**: Prometheus metrics updated after each scan

### Health Checks

**Liveness Probe (`/health`):**
- Returns 200 OK if service is running
- Returns 503 if last scan encountered errors
- Use for Kubernetes liveness probe

**Readiness Probe (`/ready`):**
- Returns 200 OK after first successful scan
- Returns 503 if no successful scan yet
- Use for Kubernetes readiness probe

### Graceful Shutdown

Web mode handles SIGTERM/SIGINT for graceful shutdown:
- Completes current scan if in progress
- Stops background refresh task
- Closes HTTP server cleanly

### Performance Considerations

**Refresh Interval:**
- Should be longer than scan duration
- Should be longer than Prometheus scrape interval
- Recommended: 300-600 seconds for most deployments

**Resource Usage:**
- Memory scales with number of resources discovered
- CPU spikes during scan, idle between scans
- Adjust container limits based on number of accounts/regions

## Example Use Cases

### Alert on Non-Compliant Resources

```yaml
# Prometheus alert rules
- alert: TagComplianceViolation
  expr: tag_non_compliant_total > 0
  for: 1h
  labels:
    severity: warning
  annotations:
    summary: "{{ $labels.tag }} tag missing on {{ $value }} resources in {{ $labels.account_name }}"

- alert: LowTagCompliance
  expr: tag_compliance_percentage < 90
  for: 30m
  labels:
    severity: warning
  annotations:
    summary: "Tag '{{ $labels.tag }}' compliance at {{ $value }}% in {{ $labels.account_name }}"

- alert: EC2TagComplianceIssue
  expr: tag_resource_type_compliance_percentage{resource_type="instance"} < 95
  for: 1h
  labels:
    severity: critical
  annotations:
    summary: "EC2 instances have {{ $value }}% compliance for tag '{{ $labels.tag }}'"
```

### Compliance Dashboard

Query for overall compliance:
```promql
# Overall compliance percentage by account
compliance_percentage

# Per-tag compliance percentage (identifies problematic tags)
tag_compliance_percentage

# EC2 instance compliance by tag
tag_resource_type_compliance_percentage{resource_type="instance"}

# Total fully compliant resources
sum(resources_fully_compliant_total)

# Fully compliant resources by type (identifies resource types needing attention)
resources_fully_compliant_by_type_total

# Total non-compliant resources by tag
sum by (tag) (tag_non_compliant_total)

# Accounts with lowest compliance
topk(5, compliance_percentage)

# Worst performing tags across all accounts
bottomk(5, avg by (tag) (tag_compliance_percentage))

# Resource types with most compliance issues
bottomk(10, sum by (resource_type) (resources_fully_compliant_by_type_total))
```

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
python main.py

# Docker build
docker build -t aws-tag-exporter .

# Linting (recommended)
pip install ruff
ruff check .
```

## License

See project repository for license information.
