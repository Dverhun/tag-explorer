# Kubernetes Deployment

This directory contains Kubernetes manifests for deploying the AWS Tag Compliance Exporter as a long-running service in Kubernetes.

## Architecture

The exporter runs as a Deployment with:
- **Web server** exposing metrics on port 8080
- **Background task** that periodically scans AWS resources (default: every 5 minutes)
- **Prometheus scraping** via `/metrics` endpoint
- **Health checks** for Kubernetes liveness and readiness probes

## Files

| File | Description |
|------|-------------|
| `serviceaccount.yaml` | ServiceAccount for the pod (supports AWS IRSA) |
| `configmap.yaml` | Configuration file (accounts, regions, required tags) |
| `deployment.yaml` | Main deployment manifest |
| `service.yaml` | ClusterIP service for metrics endpoint |
| `servicemonitor.yaml` | Prometheus Operator ServiceMonitor (optional) |
| `kustomization.yaml` | Kustomize configuration for easy deployment |

## Prerequisites

### 1. AWS Credentials

Choose one of these methods:

**Option A: AWS IRSA (Recommended for EKS)**

1. Create IAM role with required permissions:
```json
{
  "Version": "2012-10-17",
  "Statement": [
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
  ]
}
```

2. Configure trust relationship for IRSA
3. Update `serviceaccount.yaml` with role ARN annotation

**Option B: AWS Credentials Secret**

1. Create secret with AWS credentials:
```bash
kubectl create secret generic aws-credentials \
  --from-file=credentials=$HOME/.aws/credentials \
  --from-file=config=$HOME/.aws/config \
  -n monitoring
```

2. Uncomment AWS credentials volume in `deployment.yaml`

### 2. Configuration

Edit `configmap.yaml` to configure:
- AWS accounts and regions to scan
- Required tags to validate
- Role assumption settings
- Resource type exclusions

### 3. Docker Image

Build and push the Docker image:

```bash
# Build image
docker build -t your-registry/aws-tag-exporter:latest .

# Push to registry
docker push your-registry/aws-tag-exporter:latest
```

Update image reference in `kustomization.yaml` or `deployment.yaml`.

## Deployment

### Using kubectl

```bash
# Create namespace
kubectl create namespace monitoring

# Apply manifests
kubectl apply -f serviceaccount.yaml
kubectl apply -f configmap.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml

# Optional: ServiceMonitor for Prometheus Operator
kubectl apply -f servicemonitor.yaml
```

### Using Kustomize

```bash
# Edit kustomization.yaml first (namespace, image, etc.)

# Deploy
kubectl apply -k .

# Or with namespace override
kubectl apply -k . -n your-namespace
```

### Using Helm (if you convert to Helm chart)

```bash
helm install aws-tag-exporter ./chart -n monitoring
```

## Verification

Check deployment status:

```bash
# Check pods
kubectl get pods -n monitoring -l app=aws-tag-exporter

# Check logs
kubectl logs -n monitoring -l app=aws-tag-exporter -f

# Check service
kubectl get svc -n monitoring aws-tag-exporter
```

Test endpoints:

```bash
# Port forward to local machine
kubectl port-forward -n monitoring svc/aws-tag-exporter 8080:8080

# Test health endpoint
curl http://localhost:8080/health

# Test metrics endpoint
curl http://localhost:8080/metrics

# Test readiness
curl http://localhost:8080/ready
```

## Prometheus Integration

### Method 1: Annotation-based Discovery

The service already has annotations for automatic discovery:

```yaml
annotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "8080"
  prometheus.io/path: "/metrics"
```

No additional configuration needed if your Prometheus is configured to discover services by annotations.

### Method 2: ServiceMonitor (Prometheus Operator)

If using Prometheus Operator:

1. Ensure ServiceMonitor CRD is installed
2. Update `servicemonitor.yaml` with correct labels matching your Prometheus `serviceMonitorSelector`
3. Apply: `kubectl apply -f servicemonitor.yaml`

### Method 3: Manual Scrape Config

Add to Prometheus configuration:

```yaml
scrape_configs:
  - job_name: 'aws-tag-exporter'
    kubernetes_sd_configs:
      - role: service
        namespaces:
          names: ['monitoring']
    relabel_configs:
      - source_labels: [__meta_kubernetes_service_label_app]
        action: keep
        regex: aws-tag-exporter
      - source_labels: [__meta_kubernetes_service_port_name]
        action: keep
        regex: metrics
```

## Configuration Options

### Environment Variables

You can override settings via environment variables in `deployment.yaml`:

```yaml
env:
  - name: REFRESH_INTERVAL
    value: "600"  # Scan every 10 minutes instead of default 5
```

### Command Arguments

Override via container args:

```yaml
args:
  - "--web"
  - "--port=8080"
  - "--refresh-interval=600"
```

### Resource Limits

Adjust based on number of accounts/regions:

- **Small deployment** (1-2 accounts): 256Mi memory, 100m CPU
- **Medium deployment** (3-10 accounts): 512Mi memory, 250m CPU
- **Large deployment** (10+ accounts): 1Gi memory, 500m CPU

## Scaling Considerations

- **Single replica only**: The exporter should run as a single instance to avoid duplicate scans
- **Refresh interval**: Adjust based on:
  - Number of accounts and regions
  - Rate limits on AWS APIs
  - Prometheus scrape interval (should be less than refresh interval)
- **AWS API limits**: Each scan makes multiple API calls. Monitor for throttling errors in logs.

## Monitoring

Key metrics to monitor:

```promql
# Overall compliance percentage
compliance_percentage

# Resources scanned
resources_scanned_total

# Tag-specific compliance
tag_compliance_percentage{tag="Environment"}

# Non-compliant resources by type
tag_non_compliant_total
```

## Troubleshooting

### Pod not starting

Check logs:
```bash
kubectl logs -n monitoring -l app=aws-tag-exporter
```

Common issues:
- AWS credentials not configured correctly
- Config file syntax errors
- Missing IRSA role annotation

### Readiness probe failing

The pod won't be ready until the first successful scan completes. Check:
- AWS role has correct permissions
- All accounts are accessible
- No network connectivity issues

### Metrics not appearing in Prometheus

1. Check service endpoint is accessible:
```bash
kubectl port-forward -n monitoring svc/aws-tag-exporter 8080:8080
curl http://localhost:8080/metrics
```

2. Check Prometheus targets page for scrape errors
3. Verify ServiceMonitor labels match Prometheus selector

### High memory usage

- Reduce number of regions scanned
- Increase resource type exclusions
- Reduce refresh interval (metrics retained in memory)

## Security Best Practices

1. **Use IRSA** instead of static credentials
2. **Least privilege IAM policies** - only grant necessary permissions
3. **Network policies** - restrict pod network access
4. **Resource limits** - prevent resource exhaustion
5. **Read-only config** - mount config as read-only
6. **Namespace isolation** - deploy in dedicated namespace

## Example Queries

Useful Prometheus queries:

```promql
# Accounts with less than 80% compliance
compliance_percentage < 80

# Total non-compliant resources across all accounts
sum(tag_non_compliant_total)

# Non-compliant resources by account
sum by (account_name) (tag_non_compliant_total)

# Resources missing specific tag
tag_non_compliant_total{tag="Environment"}

# Compliance trend over time
avg_over_time(compliance_percentage[24h])
```

## Cleanup

```bash
# Delete all resources
kubectl delete -k . -n monitoring

# Or individually
kubectl delete -f service.yaml
kubectl delete -f deployment.yaml
kubectl delete -f configmap.yaml
kubectl delete -f serviceaccount.yaml
```
