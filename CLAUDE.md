# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

**AWS Tag Compliance Metrics Exporter** - A Python CLI that scans AWS resources across multiple accounts and regions, validates required tags, and exports Prometheus-compatible metrics.

**Core Function**: Single-purpose metrics exporter for AWS tag compliance monitoring.

## Quick Commands

```bash
# Run scan and output metrics to stdout
python main.py

# Export metrics to file
python main.py --output metrics.txt

# Use custom config
python main.py --config custom-config.yaml

# Docker
docker build -t aws-tag-exporter .
docker run --rm -v ~/.aws:/root/.aws:ro -v $(pwd)/config.yaml:/app/config.yaml aws-tag-exporter
```

## Architecture

### Core Components

1. **main.py** - CLI orchestrator
   - Argument parsing (--config, --output)
   - Calls `scan_and_export_metrics()` to orchestrate workflow
   - Outputs metrics to stdout or file

2. **src/aws_audit.py** - AWS resource discovery and validation
   - `validate_resource_tags()`: Main entry point
   - Uses STS AssumeRole for cross-account access
   - Resource Groups Tagging API for resource discovery
   - Returns: `Dict[account_id -> {account_name, regions -> {compliant, non_compliant, total, errors}}]`

3. **src/metrics.py** - Prometheus metrics
   - Metric definitions: TAG_COMPLIANT, TAG_NON_COMPLIANT, TAG_MISSING_DETAIL, RESOURCES_SCANNED, COMPLIANCE_PERCENTAGE
   - `update_metrics()`: Updates gauges from scan results
   - `expose_prometheus_metrics()`: Generates Prometheus format output

### Data Flow

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