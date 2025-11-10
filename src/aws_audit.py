"""AWS resource discovery and tag validation.

Uses AWS Resource Groups Tagging API for cross-account resource discovery
and tag compliance validation.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def _assume_role(sts_client, role_arn: str, session_name: str = "tag-audit") -> Dict[str, str]:
    """Assume IAM role and return temporary credentials."""
    resp = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
        DurationSeconds=3600
    )
    creds = resp["Credentials"]
    return {
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"],
    }


def _get_tagging_client(region: str, creds: Optional[Dict[str, str]] = None):
    """Create Resource Groups Tagging API client."""
    config = Config(
        retries={"max_attempts": 5, "mode": "standard"},
        connect_timeout=10,
        read_timeout=60
    )
    kwargs = {"region_name": region, "config": config}
    if creds:
        kwargs.update(**creds)
    return boto3.client("resourcegroupstaggingapi", **kwargs)


def _extract_tags(tag_list: List[Dict[str, str]]) -> Dict[str, str]:
    """Convert AWS tag list format to dict."""
    return {tag["Key"]: tag.get("Value", "") for tag in tag_list}


def _parse_resource_arn(arn: str) -> Tuple[str, str]:
    """Parse ARN to extract service and resource type.

    ARN format: arn:partition:service:region:account:resource
    Returns: (service, resource_type)
    """
    if not arn or ":" not in arn:
        return "unknown", "unknown"

    try:
        parts = arn.split(":", 5)
        service = parts[2] if len(parts) > 2 else "unknown"
        resource_part = parts[5] if len(parts) > 5 else ""
        resource_type = resource_part.split("/")[0] if "/" in resource_part else service
        return service, resource_type
    except Exception as e:
        logger.warning("Failed to parse ARN %s: %s", arn, e)
        return "unknown", "unknown"


def _is_excluded(resource_type: str, exclusion_patterns: List[str]) -> bool:
    """Check if resource type matches any exclusion pattern.

    Supports:
    - Exact match: "pod"
    - Substring match: pattern in resource_type
    - Service prefix: "ecs:task" matches "task" for ECS service
    - Wildcard: "eks:*" matches all resource types containing "eks:"
    """
    if not exclusion_patterns:
        return False

    resource_type_lower = resource_type.lower()

    for pattern in exclusion_patterns:
        pattern_lower = pattern.lower()

        # Wildcard match (e.g., "eks:*")
        if "*" in pattern_lower:
            prefix = pattern_lower.replace("*", "")
            if prefix in resource_type_lower:
                return True

        # Exact or substring match
        elif pattern_lower in resource_type_lower:
            return True

    return False


def validate_resource_tags(
    aws_account_matrix: List[Dict[str, Any]],
    required_tags: List[str],
    assume_role_name_template: Optional[str] = None,
    account_overrides: Optional[Dict[str, Dict[str, str]]] = None,
    excluded_resource_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Validate tags across AWS accounts and regions.

    Args:
        aws_account_matrix: List of account configurations with account_id, account_name, regions
        required_tags: List of required tag names
        assume_role_name_template: Role name template with {account_id} placeholder
        account_overrides: Account-specific role ARN overrides
        excluded_resource_types: List of resource type patterns to exclude (supports wildcards)

    Returns:
        Dict[account_id -> {account_name, regions -> {compliant, non_compliant, total, errors}}]
    """
    results = {}
    account_overrides = account_overrides or {}
    excluded_resource_types = excluded_resource_types or []
    base_sts = boto3.client("sts")

    if excluded_resource_types:
        logger.info("Excluding resource types: %s", excluded_resource_types)

    for acct in aws_account_matrix:
        account_id = acct["account_id"]
        account_name = acct.get("account_name", account_id)
        regions = acct.get("regions") or ["us-east-1"]

        logger.info("="*60)
        logger.info("Scanning %s (%s) - regions: %s", account_name, account_id, ", ".join(regions))

        creds = _get_account_credentials(
            base_sts, account_id, assume_role_name_template, account_overrides
        )

        if creds is None:
            results[account_id] = {
                "account_id": account_id,
                "account_name": account_name,
                "error": "Failed to obtain credentials",
                "regions": {}
            }
            continue

        account_result = {
            "account_id": account_id,
            "account_name": account_name,
            "regions": {}
        }

        for region in regions:
            logger.info("Scanning region: %s", region)
            account_result["regions"][region] = _scan_region(
                region, creds, required_tags, account_id, account_name, excluded_resource_types
            )

        results[account_id] = account_result

    logger.info("="*60)
    logger.info("Scan complete: %d accounts", len(results))
    logger.info("="*60)

    return results


def _get_account_credentials(
    sts_client,
    account_id: str,
    assume_role_template: Optional[str],
    overrides: Dict[str, Dict[str, str]]
) -> Optional[Dict[str, str]]:
    """Get credentials for account via role assumption."""
    override = overrides.get(account_id, {})
    role_arn = override.get("role_arn")

    if not role_arn and assume_role_template:
        role_name = assume_role_template.format(account_id=account_id)
        role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

    if not role_arn:
        return None

    try:
        logger.info("Assuming role: %s", role_arn)
        return _assume_role(sts_client, role_arn, session_name=f"audit-{account_id}")
    except ClientError as e:
        logger.error("Failed to assume role %s: %s", role_arn, e)
        return None


def _scan_region(
    region: str,
    creds: Dict[str, str],
    required_tags: List[str],
    account_id: str,
    account_name: str,
    excluded_resource_types: List[str]
) -> Dict[str, Any]:
    """Scan single region for tag compliance."""
    result = {"compliant": [], "non_compliant": [], "total": 0, "excluded": 0, "errors": []}

    try:
        client = _get_tagging_client(region, creds)
        paginator = client.get_paginator("get_resources")

        for page_num, page in enumerate(paginator.paginate(ResourcesPerPage=100), 1):
            resources = page.get("ResourceTagMappingList", [])
            logger.debug("Processing page %d: %d resources", page_num, len(resources))

            for resource in resources:
                arn = resource.get("ResourceARN", "")
                _, resource_type = _parse_resource_arn(arn)

                # Check if resource type is excluded
                if _is_excluded(resource_type, excluded_resource_types):
                    result["excluded"] += 1
                    logger.debug("Excluding resource: %s (type: %s)", arn, resource_type)
                    continue

                result["total"] += 1
                record = _validate_resource(
                    resource, required_tags, account_id, account_name, region
                )
                target = result["non_compliant"] if record["missing_tags"] else result["compliant"]
                target.append(record)

        logger.info(
            "Region %s: %d scanned, %d compliant, %d non-compliant, %d excluded",
            region, result["total"], len(result["compliant"]),
            len(result["non_compliant"]), result["excluded"]
        )

    except ClientError as e:
        error_msg = f"AWS API error in {region}: {e}"
        logger.error(error_msg)
        result["errors"].append(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error in {region}: {e}"
        logger.error(error_msg, exc_info=True)
        result["errors"].append(error_msg)

    return result


def _validate_resource(
    resource: Dict[str, Any],
    required_tags: List[str],
    account_id: str,
    account_name: str,
    region: str
) -> Dict[str, Any]:
    """Validate single resource against required tags."""
    arn = resource.get("ResourceARN", "")
    service, resource_type = _parse_resource_arn(arn)
    tags = _extract_tags(resource.get("Tags", []))

    present = [tag for tag in required_tags if tag in tags]
    missing = [tag for tag in required_tags if tag not in tags]

    return {
        "account_id": account_id,
        "account_name": account_name,
        "region": region,
        "resource_arn": arn,
        "resource_type": resource_type,
        "service": service,
        "present_tags": present,
        "missing_tags": missing,
        "raw_tags": tags,
    }