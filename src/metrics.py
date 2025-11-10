"""
Prometheus metrics helpers.
Exposes compliance metrics in Prometheus format.
"""
from prometheus_client import Gauge, Counter, Info, Histogram, generate_latest, CONTENT_TYPE_LATEST
from typing import Dict, Any
from fastapi import Response
import logging
from datetime import datetime, timezone
import re

logger = logging.getLogger(__name__)

# Metric definitions as specified in project requirements
TAG_COMPLIANT = Gauge(
    "tag_compliant_total",
    "Number of resources compliant per tag",
    ["tag", "account_name", "account_id", "region"],
)

TAG_NON_COMPLIANT = Gauge(
    "tag_non_compliant_total",
    "Number of resources non-compliant per tag",
    ["tag", "account_name", "account_id", "region"],
)

TAG_MISSING_DETAIL = Gauge(
    "tag_missing_detail",
    "Detail gauge set to 1 for a missing-tag/resource combination",
    ["tag", "account_name", "account_id", "region", "resource_type", "resource_arn"],
)

RESOURCES_SCANNED = Gauge(
    "resources_scanned_total",
    "Total number of resources scanned",
    ["account_name", "account_id", "region"],
)

COMPLIANCE_PERCENTAGE = Gauge(
    "compliance_percentage",
    "Overall compliance percentage by account and region",
    ["account_name", "account_id", "region"],
)

SCAN_DURATION = Gauge(
    "scan_duration_seconds",
    "Duration of last scan in seconds",
    ["account_name", "account_id"],
)

SCAN_TIMESTAMP = Gauge(
    "scan_timestamp_seconds",
    "Timestamp of last successful scan",
    ["account_name", "account_id"],
)


def update_metrics(compliance_data: Dict[str, Any]):
    """
    Update Prometheus metrics based on compliance scan results.
    
    Args:
        compliance_data: Dict of compliance results keyed by account_id
    """
    logger.info("Updating Prometheus metrics")
    
    # Clear detail metrics to avoid stale data
    TAG_MISSING_DETAIL.clear()
    
    for account_id, acct in compliance_data.items():
        if "error" in acct:
            logger.warning("Skipping metrics for account %s due to error", account_id)
            continue
            
        acct_name = acct.get("account_name", account_id)
        
        for region, data in acct.get("regions", {}).items():
            total = data.get("total", 0)
            compliant_count = len(data.get("compliant", []))
            non_compliant_count = len(data.get("non_compliant", []))
            
            # Overall metrics
            RESOURCES_SCANNED.labels(
                account_name=acct_name,
                account_id=account_id,
                region=region
            ).set(total)
            
            compliance_pct = (compliant_count / total * 100) if total > 0 else 0
            COMPLIANCE_PERCENTAGE.labels(
                account_name=acct_name,
                account_id=account_id,
                region=region
            ).set(compliance_pct)
            
            # Per-tag compliance metrics
            tag_compliant_counts = {}
            tag_missing_counts = {}
            
            # Process non-compliant resources
            for rec in data.get("non_compliant", []):
                missing_tags = rec.get("missing_tags", [])
                present_tags = rec.get("present_tags", [])
                resource_arn = rec.get("resource_arn", "")
                resource_type = rec.get("resource_type", "unknown")
                
                # Count missing tags
                for tag in missing_tags:
                    key = (tag, acct_name, account_id, region)
                    tag_missing_counts[key] = tag_missing_counts.get(key, 0) + 1
                    
                    # Set detail metric for this specific missing tag/resource combo
                    # Truncate ARN if too long to avoid cardinality issues
                    arn_label = resource_arn[:200] if len(resource_arn) > 200 else resource_arn
                    try:
                        TAG_MISSING_DETAIL.labels(
                            tag=tag,
                            account_name=acct_name,
                            account_id=account_id,
                            region=region,
                            resource_type=resource_type,
                            resource_arn=arn_label
                        ).set(1)
                    except Exception as e:
                        logger.warning("Failed to set detail metric for %s: %s", resource_arn, e)
                
                # Count present tags from non-compliant resources
                for tag in present_tags:
                    key = (tag, acct_name, account_id, region)
                    tag_compliant_counts[key] = tag_compliant_counts.get(key, 0) + 1
            
            # Process compliant resources
            for rec in data.get("compliant", []):
                present_tags = rec.get("present_tags", [])
                for tag in present_tags:
                    key = (tag, acct_name, account_id, region)
                    tag_compliant_counts[key] = tag_compliant_counts.get(key, 0) + 1
            
            # Update tag-level gauges
            for (tag, acct_name, account_id, region), count in tag_compliant_counts.items():
                TAG_COMPLIANT.labels(
                    tag=tag,
                    account_name=acct_name,
                    account_id=account_id,
                    region=region
                ).set(count)
            
            for (tag, acct_name, account_id, region), count in tag_missing_counts.items():
                TAG_NON_COMPLIANT.labels(
                    tag=tag,
                    account_name=acct_name,
                    account_id=account_id,
                    region=region
                ).set(count)
    
    logger.info("Metrics update complete")


def expose_prometheus_metrics():
    """
    Generate Prometheus metrics endpoint response.
    
    Returns:
        FastAPI Response with Prometheus metrics
    """
    try:
        data = generate_latest()
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)
    except Exception as e:
        logger.error("Failed to generate metrics: %s", e, exc_info=True)
        return Response(
            content=f"# Error generating metrics: {e}\n",
            media_type="text/plain",
            status_code=500
        )


def reset_metrics():
    """Reset all metrics (useful for testing or complete rescans)."""
    logger.info("Resetting all metrics")
    TAG_COMPLIANT.clear()
    TAG_NON_COMPLIANT.clear()
    TAG_MISSING_DETAIL.clear()
    RESOURCES_SCANNED.clear()
    COMPLIANCE_PERCENTAGE.clear()