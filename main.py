"""AWS Resource Tag Compliance Metrics Exporter.

Scans AWS resources across accounts/regions, validates required tags,
and exports Prometheus-compatible metrics.
"""
import argparse
import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.aws_audit import validate_resource_tags
from src.metrics import update_metrics, expose_prometheus_metrics

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def scan_and_export_metrics(config_path: str = "config.yaml", output_file: str = None):
    """Execute scan and export Prometheus metrics."""
    logger.info("Loading configuration from %s", config_path)
    cfg = load_config(config_path)

    matrix = cfg.get('aws_account_matrix', [])
    required_tags = cfg.get('REQUIRED_TAGS', [])
    assume_template = cfg.get('assume_role_name_template')
    overrides = cfg.get('aws_account_overrides', {})

    logger.info("Starting AWS resource scan across %d accounts", len(matrix))
    logger.info("Required tags: %s", required_tags)

    results = validate_resource_tags(matrix, required_tags, assume_template, overrides)

    logger.info("Updating Prometheus metrics")
    update_metrics(results)

    _print_summary(results)

    if output_file:
        metrics_data = expose_prometheus_metrics()
        with open(output_file, 'wb') as f:
            f.write(metrics_data.body)
        logger.info("Metrics exported to %s", output_file)
    else:
        print("\n" + "="*80)
        print("PROMETHEUS METRICS")
        print("="*80)
        metrics_data = expose_prometheus_metrics()
        print(metrics_data.body.decode('utf-8'))

    return results


def _print_summary(results: dict):
    """Print scan summary to console."""
    print("\n" + "="*80)
    print("SCAN SUMMARY")
    print("="*80)

    for account_id, acct in results.items():
        if "error" in acct:
            print(f"\nAccount: {acct.get('account_name')} ({account_id}) - ERROR: {acct['error']}")
            continue

        acct_name = acct.get('account_name')
        print(f"\nAccount: {acct_name} ({account_id})")

        for region, data in acct.get('regions', {}).items():
            total = data.get('total', 0)
            compliant = len(data.get('compliant', []))
            non_compliant = len(data.get('non_compliant', []))
            compliance_pct = (compliant / total * 100) if total > 0 else 0

            print(f"  Region: {region}")
            print(f"    Total: {total} | Compliant: {compliant} | "
                  f"Non-Compliant: {non_compliant} | Compliance: {compliance_pct:.1f}%")


def main():
    parser = argparse.ArgumentParser(
        description="AWS Resource Tag Compliance Metrics Exporter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan and print metrics to stdout
  python main.py

  # Export metrics to file
  python main.py --output metrics.txt

  # Use custom config
  python main.py --config custom-config.yaml
        """
    )

    parser.add_argument(
        '--config',
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )
    parser.add_argument(
        '--output',
        help='Output file for metrics (default: stdout)'
    )

    args = parser.parse_args()

    try:
        scan_and_export_metrics(args.config, args.output)
    except Exception as e:
        logger.error("Scan failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()