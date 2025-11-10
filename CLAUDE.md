# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AWS Resource Tag Compliance Auditor - A Python application that scans AWS resources across multiple accounts and regions, validates required tags, generates Excel compliance reports, and exposes Prometheus-compatible metrics with a FastAPI web interface.

## Development Commands

### Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run a one-time scan and generate reports
python main.py scan

# Start the web server (includes initial scan)
python main.py serve

# Start web server on custom port
python main.py serve --port 8080

# Use custom config file
python main.py scan --config custom-config.yaml
```

### Docker

```bash
# Build Docker image
docker build -t aws-tag-auditor .

# Run container (method 1: standard)
docker run -p 8000:8000 \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd)/reports:/app/reports \
  -v $(pwd)/config.yaml:/app/config.yaml \
  aws-tag-auditor

# Run container (method 2: using build script)
./build-and-run.sh  # Loads AWS credentials from ./metadata/*.sh files
```

### Testing and Validation

```bash
# Check FastAPI docs (after starting server)
curl http://localhost:8000/docs

# Health check
curl http://localhost:8000/health

# View dashboard data
curl http://localhost:8000/dashboard

# Trigger manual rescan (without reports)
curl -X POST http://localhost:8000/rescan

# Generate reports on-demand
curl -X POST http://localhost:8000/generate-reports

# Download specific report
curl "http://localhost:8000/reports/download?account_id=123456789012&which=non_compliant" -o report.xlsx

# View Prometheus metrics
curl http://localhost:8000/metrics
```

## Architecture

### Core Components

The application follows a modular architecture with clear separation of concerns:

1. **main.py** - CLI orchestrator and entry point
   - Handles command-line arguments (`scan` vs `serve` commands)
   - Loads config.yaml and orchestrates the scanning workflow
   - Provides `run_scan()` for one-time scans and `start_web_server()` for API mode

2. **src/aws_audit.py** - AWS resource discovery and tag validation
   - Uses AWS Resource Groups Tagging API for broad resource discovery across services
   - Implements STS AssumeRole pattern for cross-account access
   - Main function: `validate_resource_tags()` returns compliance data structure
   - Returns nested dict: `{account_id: {regions: {region_name: {compliant: [], non_compliant: [], total: int}}}}`

3. **src/excel_exporter.py** - Analytics-optimized Excel report generation
   - Generates multi-sheet Excel workbooks with pivot table support
   - Creates consolidated reports across all accounts plus per-account reports
   - Key sheets: Master_Data, Summary_by_Account, Summary_by_Service, Summary_by_Tag, Violations_Only
   - Flattens nested data into analytics-friendly format with individual tag status columns
   - Categorizes resources into families (Compute, Storage, Database, Network, etc.)

4. **src/metrics.py** - Prometheus metrics exposure
   - Defines metrics: `tag_compliant_total`, `tag_non_compliant_total`, `tag_missing_detail`, `resources_scanned_total`, `compliance_percentage`
   - Labels include: account_name, account_id, region, tag, resource_type, resource_arn
   - `update_metrics()` is called after each scan to refresh metric values
   - `expose_prometheus_metrics()` generates /metrics endpoint response

5. **src/web_app.py** - FastAPI web application
   - Factory pattern: `create_app()` returns configured FastAPI instance
   - Stores last scan results in global `LAST_SCAN` dict (data, reports, timestamp)
   - Startup behavior: performs initial scan, starts periodic background task (if enabled)
   - Background task: `_periodic_scan_task()` runs scans based on `metrics_gathering_interval` config
   - Key endpoints: /dashboard, /reports, /resource-details, /metrics, /rescan, /generate-reports

### Data Flow

**Scan Mode:**
1. Load config.yaml → 2. `validate_resource_tags()` discovers resources via AWS API → 3. `generate_excel_reports()` creates workbooks → 4. `update_metrics()` sets Prometheus gauges → 5. Print summary to console

**Serve Mode:**
1. Initial scan on startup → 2. Store results in `LAST_SCAN` global → 3. Start periodic background task → 4. API endpoints serve data from `LAST_SCAN` → 5. Manual `/rescan` triggers new scan without reports → 6. Manual `/generate-reports` creates Excel files from last scan data

### Configuration Architecture

**config.yaml structure:**
- `aws_account_matrix`: List of accounts to scan, each with account_id, account_name, and regions list
- `assume_role_name_template`: Template for IAM role name (e.g., "terraform" becomes "arn:aws:iam::{account_id}:role/terraform")
- `aws_account_overrides`: Optional explicit role ARNs for specific accounts (overrides template)
- `REQUIRED_TAGS`: List of tag names that must be present on all resources
- `reports_dir`: Output directory for Excel reports (default: ./reports)
- `metrics_gathering_interval`: Seconds between periodic scans (0 = disabled)

### Cross-Account Access Pattern

The application uses AWS STS AssumeRole for secure cross-account scanning:
1. Base credentials from environment/profile
2. For each account, construct role ARN from `assume_role_name_template` or use explicit override
3. Call `sts.assume_role()` to get temporary credentials
4. Use temporary credentials to create Resource Groups Tagging API client in each region
5. Paginate through `get_resources()` API to discover all tagged resources

### Periodic Scanning Behavior

When `metrics_gathering_interval > 0`:
- Background asyncio task starts on application startup
- Task sleeps for configured interval, then triggers `_perform_scan()`
- Scans update `LAST_SCAN` data and Prometheus metrics, but do NOT generate Excel reports by default
- Excel reports are only generated on explicit `/generate-reports` POST or during one-time `scan` command
- This design optimizes for metrics export while avoiding disk I/O overhead on every scan

### Error Handling Philosophy

- Account-level errors (failed AssumeRole) are captured in results dict with `"error"` key, allowing other accounts to continue
- Region-level errors append to `region_result["errors"]` list, allowing other regions to continue
- Web API endpoints check for `LAST_SCAN["data"]` existence and return 404 if no data available
- Metrics update skips accounts with errors to avoid stale/incorrect metrics

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
- Uses `resourcegroupstaggingapi` client (not service-specific APIs) for broad coverage
- Pagination required: `get_paginator("get_resources")` with `ResourcesPerPage=100`
- Resources without ANY tags are invisible to this API (AWS limitation)
- ARN parsing: `_parse_resource_arn()` extracts service and resource type from ARN format

### Excel Report Generation
- Uses pandas + openpyxl for Excel writing
- Sheet naming: Excel max 31 chars, so region names like "us-east-1" become "us_east_1"
- Tag status columns: Each required tag gets two columns: `Tag_{name}` (Present/Missing) and `Tag_{name}_Value` (actual value)
- Consolidated report includes all accounts; per-account reports filter Master_Data by Account ID
- Metadata sheet includes: report timestamp, total resources, compliance %, required tags list

### Prometheus Metrics
- `TAG_MISSING_DETAIL` is cleared before each update to avoid stale label combinations
- ARN labels are truncated to 200 chars to prevent cardinality explosion
- Tag-level metrics are aggregated from both compliant and non-compliant resources (e.g., a non-compliant resource may have SOME required tags present)
- Metrics are only updated after successful scan completion

### Web Application
- Uses FastAPI factory pattern for clean testing and uvicorn integration
- CORS enabled for all origins (suitable for internal dashboards)
- `/dashboard-ui` serves static HTML file from `src/dashboard_ui.html`
- `/rescan` returns 409 Conflict if scan already in progress (tracked via `SCAN_STATUS["is_scanning"]`)
- Background task checks config interval on each loop iteration, allowing runtime config changes

## Common Patterns

### Adding New API Endpoints
1. Add route decorator to function in `src/web_app.py` (inside `create_app()` factory)
2. Access last scan data via `LAST_SCAN.get("data")`
3. Return structured JSON or use FastAPI response classes (HTMLResponse, FileResponse, etc.)
4. Add error handling with HTTPException for missing data

### Adding New Metrics
1. Define Gauge/Counter/Histogram in `src/metrics.py` module level
2. Add update logic in `update_metrics()` function
3. Consider label cardinality (resource ARNs can be very high cardinality)
4. Clear gauge/counter in `reset_metrics()` for consistency

### Modifying Excel Report Structure
1. Update/add helper functions in `src/excel_exporter.py` (pattern: `_create_*()` returns DataFrame)
2. Modify `generate_excel_reports()` to write new sheet via `df.to_excel(writer, sheet_name=...)`
3. Update `_flatten_resource_for_analytics()` if adding new resource-level columns
4. Consider backward compatibility with existing report consumers

### Changing Required Tags
1. Update `config.yaml` REQUIRED_TAGS list
2. No code changes needed - tag validation is data-driven
3. Restart application or trigger `/rescan` to apply changes
4. Excel reports will automatically include new tag columns