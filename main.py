"""
AWS Resource Tag Compliance Auditor
Main orchestration script for running scans and starting the web server.
"""
import argparse
import yaml
import logging
from pathlib import Path
import sys
import os

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from src.aws_audit import validate_resource_tags
from src.excel_exporter import generate_excel_reports
from src.metrics import update_metrics

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config(config_path: str = "config.yaml"):
    """Load configuration from YAML file."""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def run_scan(config_path: str = "config.yaml"):
    """Execute a complete scan and generate reports."""
    logger.info("Loading configuration from %s", config_path)
    cfg = load_config(config_path)
    
    matrix = cfg.get('aws_account_matrix', [])
    required_tags = cfg.get('REQUIRED_TAGS', [])
    assume_template = cfg.get('assume_role_name_template')
    out_dir = cfg.get('reports_dir', 'reports')
    overrides = cfg.get('aws_account_overrides', {})
    
    logger.info("Starting AWS resource scan across %d accounts", len(matrix))
    logger.info("Required tags: %s", required_tags)
    
    # Run validation
    results = validate_resource_tags(
        matrix, 
        required_tags, 
        assume_template, 
        overrides
    )
    
    # Generate Excel reports
    logger.info("Generating Excel reports in %s", out_dir)
    reports = generate_excel_reports(results, out_dir=out_dir)
    
    # Update Prometheus metrics
    logger.info("Updating Prometheus metrics")
    update_metrics(results)
    
    # Print summary
    print("\n" + "="*80)
    print("SCAN SUMMARY")
    print("="*80)


    # def write_results_to_file(data: Dict[str, Any]):
    #     print("Writing results to scanning_results.json")
    #     with open("scanning_results.json", "w") as f:
    #         f.write(json.dumps(data))
    
    # write_results_to_file(results) 


    for account_id, acct in results.items():
        acct_name = acct.get('account_name')
        print(f"\nAccount: {acct_name} ({account_id})")
        for region, data in acct.get('regions', {}).items():
            total = data.get('total', 0)
            compliant = len(data.get('compliant', []))
            non_compliant = len(data.get('non_compliant', []))
            compliance_pct = (compliant / total * 100) if total > 0 else 0
            print(f"  Region: {region}")
            print(f"    Total Resources: {total}")
            print(f"    Compliant: {compliant}")
            print(f"    Non-Compliant: {non_compliant}")
            print(f"    Compliance: {compliance_pct:.1f}%")
    
    print("\n" + "="*80)
    print("REPORTS GENERATED")
    print("="*80)
    for account_id, paths in reports.items():
        print(f"\nAccount {account_id}:")
        for report_type, path in paths.items():
            print(f"  {report_type}: {path}")
    
    return results, reports


def start_web_server(host: str = "0.0.0.0", port: int = 8000, config_path: str = "config.yaml"):
    """Start the FastAPI web server."""
    import uvicorn
    
    # Set config path as environment variable for web app
    os.environ['AUDIT_CONFIG'] = config_path
    
    logger.info("Starting web server on %s:%d", host, port)
    uvicorn.run(
        "src.web_app:create_app",
        host=host,
        port=port,
        factory=True,
        log_level="info"
    )


def main():
    parser = argparse.ArgumentParser(
        description="AWS Resource Tag Compliance Auditor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run a one-time scan and generate reports
  python main.py scan
  
  # Start the web server (includes initial scan)
  python main.py serve
  
  # Start web server on custom port
  python main.py serve --port 8080
        """
    )
    
    parser.add_argument(
        'command',
        choices=['scan', 'serve'],
        help='Command to execute: scan (one-time) or serve (start web UI)'
    )
    parser.add_argument(
        '--config',
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )
    parser.add_argument(
        '--host',
        default='0.0.0.0',
        help='Web server host (default: 0.0.0.0)'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=8000,
        help='Web server port (default: 8000)'
    )
    
    args = parser.parse_args()
    
    try:
        if args.command == 'scan':
            run_scan(args.config)
        elif args.command == 'serve':
            start_web_server(args.host, args.port, args.config)
    except Exception as e:
        logger.error("Error executing command: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()