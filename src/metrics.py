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

# New compliance percentage metrics
TAG_COMPLIANCE_PERCENTAGE = Gauge(
    "tag_compliance_percentage",
    "Compliance percentage per individual tag",
    ["tag", "account_name", "account_id", "region"],
)

TAG_RESOURCE_TYPE_COMPLIANCE_PERCENTAGE = Gauge(
    "tag_resource_type_compliance_percentage",
    "Compliance percentage per tag and resource type",
    ["tag", "resource_type", "account_name", "account_id", "region"],
)

RESOURCES_FULLY_COMPLIANT = Gauge(
    "resources_fully_compliant_total",
    "Resources with all required tags present",
    ["account_name", "account_id", "region"],
)

RESOURCES_FULLY_COMPLIANT_BY_TYPE = Gauge(
    "resources_fully_compliant_by_type_total",
    "Fully compliant resources grouped by resource type",
    ["resource_type", "account_name", "account_id", "region"],
)

RESOURCES_FULLY_COMPLIANT_BY_TYPE_PERCENTAGE = Gauge(
    "resources_fully_compliant_by_type_percentage",
    "Percentage of fully compliant resources by resource type",
    ["resource_type", "account_name", "account_id", "region"],
)


def update_metrics(compliance_data: Dict[str, Any]):
    """Update Prometheus metrics from compliance scan results.

    Args:
        compliance_data: Dict[account_id -> {account_name, regions -> scan_results}]
    """
    logger.info("Updating Prometheus metrics")

    # Clear detail metrics to avoid stale data
    TAG_MISSING_DETAIL.clear()
    TAG_COMPLIANCE_PERCENTAGE.clear()
    TAG_RESOURCE_TYPE_COMPLIANCE_PERCENTAGE.clear()
    RESOURCES_FULLY_COMPLIANT_BY_TYPE.clear()
    RESOURCES_FULLY_COMPLIANT_BY_TYPE_PERCENTAGE.clear()

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

    # Update basic gauges
    for (tag, acct_name, account_id, region), count in tag_compliant_counts.items():
        TAG_COMPLIANT.labels(
            tag=tag, account_name=acct_name, account_id=account_id, region=region
        ).set(count)

    for (tag, acct_name, account_id, region), count in tag_missing_counts.items():
        TAG_NON_COMPLIANT.labels(
            tag=tag, account_name=acct_name, account_id=account_id, region=region
        ).set(count)

    # Calculate and update new compliance metrics
    _update_advanced_compliance_metrics(
        compliant, non_compliant, acct_name, account_id, region
    )


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


def _update_advanced_compliance_metrics(
    compliant: list,
    non_compliant: list,
    acct_name: str,
    account_id: str,
    region: str
):
    """Calculate and update advanced compliance percentage metrics."""
    all_resources = compliant + non_compliant
    total_resources = len(all_resources)

    if total_resources == 0:
        return

    # Track fully compliant resources
    fully_compliant_count = len(compliant)
    RESOURCES_FULLY_COMPLIANT.labels(
        account_name=acct_name, account_id=account_id, region=region
    ).set(fully_compliant_count)

    # Track compliance by resource type (fully compliant)
    type_compliant_counts = {}
    type_total_counts = {}

    for rec in compliant:
        resource_type = rec.get("resource_type", "unknown")
        key = (resource_type, acct_name, account_id, region)
        type_compliant_counts[key] = type_compliant_counts.get(key, 0) + 1
        type_total_counts[key] = type_total_counts.get(key, 0) + 1

    for rec in non_compliant:
        resource_type = rec.get("resource_type", "unknown")
        key = (resource_type, acct_name, account_id, region)
        type_total_counts[key] = type_total_counts.get(key, 0) + 1

    # Set absolute counts
    for (resource_type, acct_name, account_id, region), count in type_compliant_counts.items():
        RESOURCES_FULLY_COMPLIANT_BY_TYPE.labels(
            resource_type=resource_type,
            account_name=acct_name,
            account_id=account_id,
            region=region
        ).set(count)

    # Calculate and set percentages
    for key, total_count in type_total_counts.items():
        compliant_count = type_compliant_counts.get(key, 0)
        percentage = (compliant_count / total_count * 100) if total_count > 0 else 0

        resource_type, acct_name, account_id, region = key
        RESOURCES_FULLY_COMPLIANT_BY_TYPE_PERCENTAGE.labels(
            resource_type=resource_type,
            account_name=acct_name,
            account_id=account_id,
            region=region
        ).set(percentage)

    # Calculate per-tag compliance percentage
    tag_total_counts = {}
    tag_compliant_counts = {}

    for rec in all_resources:
        present_tags = rec.get("present_tags", [])
        missing_tags = rec.get("missing_tags", [])
        all_tags = set(present_tags + missing_tags)

        for tag in all_tags:
            key = (tag, acct_name, account_id, region)
            tag_total_counts[key] = tag_total_counts.get(key, 0) + 1
            if tag in present_tags:
                tag_compliant_counts[key] = tag_compliant_counts.get(key, 0) + 1

    # Set per-tag compliance percentage
    for key in tag_total_counts:
        compliant_count = tag_compliant_counts.get(key, 0)
        total_count = tag_total_counts[key]
        percentage = (compliant_count / total_count * 100) if total_count > 0 else 0

        tag, acct_name, account_id, region = key
        TAG_COMPLIANCE_PERCENTAGE.labels(
            tag=tag, account_name=acct_name, account_id=account_id, region=region
        ).set(percentage)

    # Calculate per-tag per-resource-type compliance percentage
    tag_type_total_counts = {}
    tag_type_compliant_counts = {}

    for rec in all_resources:
        resource_type = rec.get("resource_type", "unknown")
        present_tags = rec.get("present_tags", [])
        missing_tags = rec.get("missing_tags", [])
        all_tags = set(present_tags + missing_tags)

        for tag in all_tags:
            key = (tag, resource_type, acct_name, account_id, region)
            tag_type_total_counts[key] = tag_type_total_counts.get(key, 0) + 1
            if tag in present_tags:
                tag_type_compliant_counts[key] = tag_type_compliant_counts.get(key, 0) + 1

    # Set per-tag per-resource-type compliance percentage
    for key in tag_type_total_counts:
        compliant_count = tag_type_compliant_counts.get(key, 0)
        total_count = tag_type_total_counts[key]
        percentage = (compliant_count / total_count * 100) if total_count > 0 else 0

        tag, resource_type, acct_name, account_id, region = key
        TAG_RESOURCE_TYPE_COMPLIANCE_PERCENTAGE.labels(
            tag=tag,
            resource_type=resource_type,
            account_name=acct_name,
            account_id=account_id,
            region=region
        ).set(percentage)


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