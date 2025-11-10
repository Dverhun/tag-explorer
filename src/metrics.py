"""Prometheus metrics for AWS tag compliance.

Exports tag compliance metrics in Prometheus format with labels for
account, region, tag name, resource type, and ARN.
"""
import logging
from typing import Any, Dict

from fastapi import Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest

logger = logging.getLogger(__name__)

# Prometheus metric definitions
TAG_COMPLIANT = Gauge(
    "tag_compliant_total",
    "Resources compliant with required tag",
    ["tag", "account_name", "account_id", "region"],
)

TAG_NON_COMPLIANT = Gauge(
    "tag_non_compliant_total",
    "Resources missing required tag",
    ["tag", "account_name", "account_id", "region"],
)

TAG_MISSING_DETAIL = Gauge(
    "tag_missing_detail",
    "Detailed missing tag indicator (1 per resource/tag combination)",
    ["tag", "account_name", "account_id", "region", "resource_type", "resource_arn"],
)

RESOURCES_SCANNED = Gauge(
    "resources_scanned_total",
    "Total resources scanned",
    ["account_name", "account_id", "region"],
)

COMPLIANCE_PERCENTAGE = Gauge(
    "compliance_percentage",
    "Overall tag compliance percentage",
    ["account_name", "account_id", "region"],
)


def update_metrics(compliance_data: Dict[str, Any]):
    """Update Prometheus metrics from compliance scan results.

    Args:
        compliance_data: Dict[account_id -> {account_name, regions -> scan_results}]
    """
    logger.info("Updating Prometheus metrics")
    TAG_MISSING_DETAIL.clear()

    for account_id, acct in compliance_data.items():
        if "error" in acct:
            logger.warning("Skipping account %s: %s", account_id, acct["error"])
            continue

        acct_name = acct.get("account_name", account_id)

        for region, data in acct.get("regions", {}).items():
            _update_region_metrics(account_id, acct_name, region, data)

    logger.info("Metrics updated")


def _update_region_metrics(account_id: str, acct_name: str, region: str, data: Dict[str, Any]):
    """Update metrics for single region."""
    total = data.get("total", 0)
    compliant = data.get("compliant", [])
    non_compliant = data.get("non_compliant", [])

    RESOURCES_SCANNED.labels(
        account_name=acct_name, account_id=account_id, region=region
    ).set(total)

    compliance_pct = (len(compliant) / total * 100) if total > 0 else 0
    COMPLIANCE_PERCENTAGE.labels(
        account_name=acct_name, account_id=account_id, region=region
    ).set(compliance_pct)

    tag_compliant_counts = {}
    tag_missing_counts = {}

    # Aggregate tag-level metrics
    for rec in non_compliant:
        _process_non_compliant_resource(
            rec, acct_name, account_id, region, tag_compliant_counts, tag_missing_counts
        )

    for rec in compliant:
        for tag in rec.get("present_tags", []):
            key = (tag, acct_name, account_id, region)
            tag_compliant_counts[key] = tag_compliant_counts.get(key, 0) + 1

    # Update gauges
    for (tag, acct_name, account_id, region), count in tag_compliant_counts.items():
        TAG_COMPLIANT.labels(
            tag=tag, account_name=acct_name, account_id=account_id, region=region
        ).set(count)

    for (tag, acct_name, account_id, region), count in tag_missing_counts.items():
        TAG_NON_COMPLIANT.labels(
            tag=tag, account_name=acct_name, account_id=account_id, region=region
        ).set(count)


def _process_non_compliant_resource(
    rec: Dict[str, Any],
    acct_name: str,
    account_id: str,
    region: str,
    tag_compliant_counts: Dict,
    tag_missing_counts: Dict
):
    """Process non-compliant resource for metrics."""
    missing_tags = rec.get("missing_tags", [])
    present_tags = rec.get("present_tags", [])
    resource_arn = rec.get("resource_arn", "")
    resource_type = rec.get("resource_type", "unknown")

    for tag in missing_tags:
        key = (tag, acct_name, account_id, region)
        tag_missing_counts[key] = tag_missing_counts.get(key, 0) + 1

        # Detailed metric per resource (truncate ARN to avoid cardinality explosion)
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
            logger.warning("Failed to set detail metric for %s: %s", arn_label, e)

    for tag in present_tags:
        key = (tag, acct_name, account_id, region)
        tag_compliant_counts[key] = tag_compliant_counts.get(key, 0) + 1


def expose_prometheus_metrics() -> Response:
    """Generate Prometheus metrics response."""
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