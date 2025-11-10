# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

**AWS Tag Compliance Metrics Exporter** - A Python application that scans AWS resources across multiple accounts and regions, validates required tags, and exports Prometheus-compatible metrics.

**Core Function**: Single-purpose metrics exporter for AWS tag compliance monitoring.

**Modes**:
- **CLI Mode**: One-time scan, outputs to stdout or file
- **Web Mode**: Long-running HTTP server for Kubernetes/Prometheus (default in Docker)

## Quick Commands

```bash
# CLI Mode - Run scan and output metrics to stdout
python main.py

# Export metrics to file
python main.py --output metrics.txt

# Use custom config
python main.py --config custom-config.yaml

# Web Mode - Run as HTTP server (for Kubernetes)
python main.py --web
python main.py --web --port 9090 --refresh-interval 600

# Docker - Web Mode (default)
docker build -t aws-tag-exporter .
docker run -d -p 8080:8080 -v ~/.aws:/root/.aws:ro -v $(pwd)/config.yaml:/app/config.yaml aws-tag-exporter

# Docker - CLI Mode
docker run --rm -v ~/.aws:/root/.aws:ro -v $(pwd)/config.yaml:/app/config.yaml aws-tag-exporter python main.py

# Kubernetes
kubectl apply -k k8s/
```

## Architecture

### Core Components

1. **main.py** - Application entry point
   - Argument parsing (--config, --output, --web, --port, --refresh-interval)
   - CLI mode: Calls `scan_and_export_metrics()` for one-time scan
   - Web mode: Calls `run_web_server()` for long-running HTTP server
   - Outputs metrics to stdout, file, or HTTP endpoint

2. **src/aws_audit.py** - AWS resource discovery and validation
   - `validate_resource_tags()`: Main entry point
   - Uses STS AssumeRole for cross-account access
   - Resource Groups Tagging API for resource discovery
   - Returns: `Dict[account_id -> {account_name, regions -> {compliant, non_compliant, total, errors}}]`

3. **src/metrics.py** - Prometheus metrics
   - Metric definitions: TAG_COMPLIANT, TAG_NON_COMPLIANT, TAG_MISSING_DETAIL, RESOURCES_SCANNED, COMPLIANCE_PERCENTAGE
   - `update_metrics()`: Updates gauges from scan results
   - `expose_prometheus_metrics()`: Generates Prometheus format output

4. **src/web_server.py** - Web mode (FastAPI server)
   - FastAPI app with `/metrics`, `/health`, `/ready`, `/` endpoints
   - `MetricsRefreshManager`: Background task for periodic AWS scans
   - `run_web_server()`: Main entry point for web mode
   - Graceful shutdown handling (SIGTERM/SIGINT)

### Data Flow

**CLI Mode (One-time scan):**
```
config.yaml
    ↓
main.py → load_config()
    ↓
aws_audit.validate_resource_tags()
    ├→ STS AssumeRole per account
    ├→ Resource Groups Tagging API per region
    └→ Tag validation (present/missing tags)
    ↓
metrics.update_metrics()
    └→ Update Prometheus gauges
    ↓
metrics.expose_prometheus_metrics()
    └→ Output to stdout or file
```

**Web Mode (Long-running):**
```
config.yaml
    ↓
main.py → load_config() → run_web_server()
    ↓
FastAPI app starts
    ├→ Expose /metrics endpoint
    ├→ Expose /health, /ready endpoints
    └→ Start MetricsRefreshManager background task
        ↓
    Background loop (every refresh_interval seconds):
        ↓
    aws_audit.validate_resource_tags()
        ├→ STS AssumeRole per account
        ├→ Resource Groups Tagging API per region
        └→ Tag validation (present/missing tags)
        ↓
    metrics.update_metrics()
        └→ Update Prometheus gauges in memory
        ↓
    HTTP GET /metrics → expose_prometheus_metrics()
        └→ Return latest metrics to Prometheus
```

### Configuration Structure

**config.yaml:**
- `aws_account_matrix`: List of accounts with account_id, account_name, regions
- `assume_role_name_template`: Role name template (e.g., "terraform" → "arn:aws:iam::{account_id}:role/terraform")
- `aws_account_overrides`: Optional explicit role ARNs per account
- `REQUIRED_TAGS`: List of tag names to validate
- `excluded_resource_types`: Optional list of resource type patterns to exclude (supports wildcards)

### Cross-Account Access Pattern

Pattern: STS AssumeRole
1. Base credentials from environment/AWS profile
2. Construct role ARN from template: `arn:aws:iam::{account_id}:role/{role_name}`
3. `sts.assume_role()` returns temporary credentials (1 hour)
4. Use temp credentials for Resource Groups Tagging API client

### Error Handling

- **Account-level errors**: Captured in results with `"error"` key, other accounts continue
- **Region-level errors**: Appended to `region_result["errors"]`, other regions continue
- **Metrics**: Accounts with errors excluded from metric updates

## AWS Permissions Requirements

The base credentials (or assumed role) must have:
```json
{
  "Effect": "Allow",
  "Action": [
    "tag:GetResources",
    "tag:GetTagKeys",
    "tag:GetTagValues",
    "sts:AssumeRole"
  ],
  "Resource": "*"
}
```

For cross-account roles (in target accounts), configure trust relationship:
```json
{
  "Effect": "Allow",
  "Principal": {
    "AWS": "arn:aws:iam::{base-account-id}:root"
  },
  "Action": "sts:AssumeRole"
}
```

## GitLab CI/CD

The repository uses GitLab CI with external templates:
- Pipeline config: `.gitlab-ci.yml`
- Includes: `devops/ci/templates/gitops/pipelines/.gitlab-ci.typical-werf-app.yml@v1.1.19`
- Stages: container, security, cleanup

## Key Implementation Details

### Resource Discovery

**API**: AWS Resource Groups Tagging API (`resourcegroupstaggingapi`)
- Single API for all resource types (EC2, S3, RDS, Lambda, etc.)
- Pagination: `get_paginator("get_resources")` with `ResourcesPerPage=100`
- **Limitation**: Only discovers resources with at least one tag

### Tag Validation

For each resource:
- Extract tags from API response: `[{Key, Value}]` → `{key: value}`
- Compare against `REQUIRED_TAGS`
- Classify as compliant (all tags present) or non-compliant (any tag missing)
- Store: `present_tags`, `missing_tags`, `raw_tags`

### Prometheus Metrics

**Metric Types**: All Gauges

**Basic Metrics:**

| Metric | Labels | Description |
|--------|--------|-------------|
| `tag_compliant_total` | tag, account_name, account_id, region | Count of resources with tag present |
| `tag_non_compliant_total` | tag, account_name, account_id, region | Count of resources missing tag |
| `tag_missing_detail` | + resource_type, resource_arn | Per-resource missing tag indicator |
| `resources_scanned_total` | account_name, account_id, region | Total resources scanned |
| `compliance_percentage` | account_name, account_id, region | Overall compliance % |

**Advanced Compliance Metrics:**

| Metric | Labels | Description |
|--------|--------|-------------|
| `tag_compliance_percentage` | tag, account_name, account_id, region | Compliance % per individual tag |
| `tag_resource_type_compliance_percentage` | tag, resource_type, account_name, account_id, region | Compliance % per tag and resource type |
| `resources_fully_compliant_total` | account_name, account_id, region | Resources with ALL required tags |
| `resources_fully_compliant_by_type_total` | resource_type, account_name, account_id, region | Fully compliant resources by type |

**Cardinality Management**:
- ARN labels truncated to 200 chars
- Detail metrics cleared before each update to avoid stale data
- Advanced metrics calculated in `_update_advanced_compliance_metrics()`

## Code Style

- **Senior-level**: Modular, DRY, clear separation of concerns
- **Laconic**: Concise docstrings and variable names
- **Type hints**: All function signatures
- **Logging**: Structured logging with appropriate levels

## Adding Features

### New Metric

1. Define in `src/metrics.py`: `NEW_METRIC = Gauge("name", "description", [labels])`
2. Update in `update_metrics()` or helper function
3. Consider cardinality impact

### New Configuration Option

1. Add to `config.yaml`
2. Extract in `main.py:load_config()`
3. Pass to `validate_resource_tags()` if needed
4. Update README.md

### Custom Tag Validation Logic

Modify `src/aws_audit.py:_validate_resource()` to change validation logic.

## Testing Locally

```bash
# Requires AWS credentials with appropriate permissions
export AWS_PROFILE=your-profile

# Test with minimal config
python main.py --config test-config.yaml

# Verify metrics format
python main.py | grep "^tag_"
```

## Dependencies

Minimal dependencies (see `requirements.txt`):
- boto3/botocore: AWS API clients
- prometheus-client: Metrics generation
- pyyaml: Config parsing
- fastapi: Used only for Response type in metrics module

## Common Patterns

### Debugging Failed AssumeRole

Check:
1. Base credentials have `sts:AssumeRole` permission
2. Target role ARN is correct (account ID, role name)
3. Target role trust policy allows base account
4. Target role has `tag:GetResources` permission

### Adding New Region

Add to account's `regions` list in `config.yaml`:
```yaml
- account_id: "123456789012"
  account_name: "production"
  regions: ["us-east-1", "eu-west-1", "ap-southeast-1"]  # Add here
```

### Excluding Resource Types

Configure exclusion patterns in `config.yaml`:
```yaml
excluded_resource_types:
  - "pod"           # Substring match
  - "ecs:task"      # Specific type
  - "eks:*"         # Wildcard for all EKS resources
```

Pattern matching in `_is_excluded()`:
- Case-insensitive substring matching
- Wildcard support with `*`
- Applied during resource iteration in `_scan_region()`

Excluded resources:
- Not counted in `total` metric
- Logged at debug level
- Tracked separately in `excluded` count

## Web Mode (Kubernetes Deployment)

### Overview

Web mode transforms the exporter from a one-time CLI tool into a long-running HTTP service suitable for Kubernetes deployment.

**Key Features:**
- FastAPI web server exposing `/metrics` endpoint
- Background task for periodic AWS resource scanning
- Health and readiness probes for Kubernetes
- Graceful shutdown on SIGTERM/SIGINT
- In-memory Prometheus metrics updated periodically

### Endpoints

| Endpoint | Purpose | Kubernetes Use |
|----------|---------|----------------|
| `GET /metrics` | Prometheus metrics | Scrape target |
| `GET /health` | Liveness check | livenessProbe |
| `GET /ready` | Readiness check | readinessProbe |
| `GET /` | Status information | Manual debugging |

### Background Refresh Task

**Implementation**: `MetricsRefreshManager` class in `src/web_server.py`

**Behavior:**
1. Runs first scan immediately on startup
2. Waits for `refresh_interval` seconds
3. Runs next scan in executor (non-blocking)
4. Updates Prometheus metrics in memory
5. Repeats until shutdown signal received

**Configuration:**
- Default interval: 300 seconds (5 minutes)
- Configurable via `--refresh-interval` flag
- Environment variable support possible

**Error Handling:**
- Scan errors logged but don't stop the service
- Last scan error exposed in `/health` endpoint
- Service remains "unhealthy" until next successful scan

### Health Checks

**Liveness (`/health`):**
- Returns 200 if service running normally
- Returns 503 if last scan failed (with error message)
- Used by Kubernetes to restart unhealthy pods

**Readiness (`/ready`):**
- Returns 200 after first successful scan completes
- Returns 503 before first successful scan
- Used by Kubernetes to know when to route traffic to pod

### Kubernetes Integration

**Deployment Files**: `k8s/` directory

**Key Components:**
- `deployment.yaml`: Pod definition with probes and resource limits
- `service.yaml`: ClusterIP service for /metrics endpoint
- `configmap.yaml`: Configuration (accounts, regions, tags)
- `serviceaccount.yaml`: ServiceAccount with IRSA annotation
- `servicemonitor.yaml`: Prometheus Operator integration (optional)

**IRSA (IAM Roles for Service Accounts):**
- Annotation in ServiceAccount: `eks.amazonaws.com/role-arn`
- Avoids need for AWS credentials secret
- Preferred method for EKS deployments

**Prometheus Scraping:**
- Method 1: Service annotations (`prometheus.io/scrape: "true"`)
- Method 2: ServiceMonitor CRD (Prometheus Operator)
- Method 3: Manual scrape config in Prometheus

### Adding Web Mode Features

**New Endpoint:**
1. Add route handler to `src/web_server.py` FastAPI app
2. Use `@app.get()` or `@app.post()` decorator
3. Return appropriate response type

**New Configuration:**
1. Add field to `config.yaml`
2. Extract in `main.py:load_config()`
3. Pass to `run_web_server()` and `MetricsRefreshManager`

**Modify Refresh Behavior:**
1. Update `MetricsRefreshManager._run_scan()` method
2. Access config via `self.config`
3. Maintain async/await pattern for I/O operations

### Performance Tuning

**Refresh Interval Selection:**
- Too short: Wastes AWS API calls, higher costs
- Too long: Stale metrics, delayed alerts
- Recommended: 300-600 seconds for most cases
- Consider: Number of accounts × regions × API rate limits

**Resource Limits:**
- Memory: Scales with number of resources discovered
- CPU: Spikes during scan, mostly idle between scans
- Suggested: 256Mi-512Mi memory, 100m-500m CPU

**Concurrency:**
- Single replica only (avoid duplicate scans)
- Deployment strategy: `Recreate` (not `RollingUpdate`)

### Debugging Web Mode

**Check logs:**
```bash
kubectl logs -f deployment/aws-tag-exporter -n monitoring
```

**Test endpoints locally:**
```bash
kubectl port-forward svc/aws-tag-exporter 8080:8080 -n monitoring
curl http://localhost:8080/
curl http://localhost:8080/health
curl http://localhost:8080/ready
curl http://localhost:8080/metrics | head -20
```

**Common issues:**
- Pod not ready: Check `/ready` endpoint, first scan may be slow
- Health failing: Check `/health` for last error message
- No metrics: Verify AWS credentials/IRSA configuration
- Stale metrics: Check refresh interval and last scan time in logs