"""
Resource discovery and tag validation module.
Uses AWS Resource Groups Tagging API to discover and validate tags across accounts.
"""
import logging
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def _assume_role(sts_client, role_arn: str, session_name: str = "tag-audit-session") -> Dict[str, str]:
    """Assume an IAM role and return temporary credentials."""
    try:
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
    except ClientError as e:
        logger.error("Failed to assume role %s: %s", role_arn, e)
        raise


def _get_tagging_client(region: str, creds: Dict[str, str] = None):
    """Create a Resource Groups Tagging API client."""
    kwargs = {
        "region_name": region, 
        "config": Config(
            retries={"max_attempts": 5, "mode": "standard"},
            connect_timeout=10,
            read_timeout=60
        )
    }
    if creds:
        kwargs.update(
            aws_access_key_id=creds["aws_access_key_id"],
            aws_secret_access_key=creds["aws_secret_access_key"],
            aws_session_token=creds["aws_session_token"],
        )
    return boto3.client("resourcegroupstaggingapi", **kwargs)


def _extract_tags(tag_list: List[Dict[str, str]]) -> Dict[str, str]:
    """Convert AWS tag list to dictionary."""
    return {t["Key"]: t.get("Value", "") for t in tag_list}


def _parse_resource_arn(arn: str) -> tuple:
    """Parse AWS ARN to extract service and resource type."""
    try:
        if not arn or ":" not in arn:
            return "unknown", arn
        
        # ARN format: arn:partition:service:region:account:resource
        parts = arn.split(":", 5)
        service = parts[2] if len(parts) > 2 else "unknown"
        
        # Extract more specific resource type if available
        resource_part = parts[5] if len(parts) > 5 else ""
        if "/" in resource_part:
            resource_type = resource_part.split("/")[0]
        else:
            resource_type = service
            
        return service, resource_type
    except Exception as e:
        logger.warning("Failed to parse ARN %s: %s", arn, e)
        return "unknown", arn


def validate_resource_tags(
    aws_account_matrix: List[Dict[str, Any]],
    REQUIRED_TAGS: List[str],
    assume_role_name_template: str = None,
    account_overrides: Dict[str, Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Validate tags across multiple AWS accounts and regions.
    
    Args:
        aws_account_matrix: List of account configurations
        REQUIRED_TAGS: List of required tag names
        assume_role_name_template: Template for role name (can use {account_id})
        account_overrides: Dict of account-specific configurations
    
    Returns:
        Dict keyed by account_id containing compliance data
    """
    results = {}
    account_overrides = account_overrides or {}
    
    try:
        base_sts = boto3.client("sts")
    except Exception as e:
        logger.error("Failed to create STS client: %s", e)
        raise

    for acct in aws_account_matrix:
        account_id = acct["account_id"]
        account_name = acct.get("account_name", account_id)
        regions = acct.get("regions", ["us-east-1"]) or ["us-east-1"]

        logger.info("="*60)
        logger.info("Scanning account: %s (%s)", account_name, account_id)
        logger.info("Regions: %s", ", ".join(regions))
        logger.info("="*60)

        # Determine credentials
        override = account_overrides.get(account_id, {})
        role_arn = override.get("role_arn")
        creds = None
        
        if not role_arn and assume_role_name_template:
            role_name = assume_role_name_template.format(account_id=account_id)
            role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

        if role_arn:
            try:
                logger.info("Assuming role: %s", role_arn)
                creds = _assume_role(base_sts, role_arn, session_name=f"audit-{account_id}")
            except Exception as e:
                logger.error("Failed to assume role for account %s: %s", account_id, e)
                results[account_id] = {
                    "account_id": account_id,
                    "account_name": account_name,
                    "error": str(e),
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
            region_result = {
                "compliant": [], 
                "non_compliant": [], 
                "total": 0,
                "errors": []
            }
            
            try:
                client = _get_tagging_client(region, creds)
                paginator = client.get_paginator("get_resources")
                page_iterator = paginator.paginate(ResourcesPerPage=100)

                for page_num, page in enumerate(page_iterator, 1):
                    resources = page.get("ResourceTagMappingList", [])
                    logger.debug("Processing page %d with %d resources", page_num, len(resources))
                    
                    for r in resources:
                        region_result["total"] += 1
                        resource_arn = r.get("ResourceARN", "")
                        service, resource_type = _parse_resource_arn(resource_arn)
                        
                        tags = _extract_tags(r.get("Tags", []))
                        present = [k for k in REQUIRED_TAGS if k in tags]
                        missing = [k for k in REQUIRED_TAGS if k not in tags]

                        resource_record = {
                            "account_id": account_id,
                            "account_name": account_name,
                            "region": region,
                            "resource_arn": resource_arn,
                            "resource_type": resource_type,
                            "service": service,
                            "present_tags": present,
                            "missing_tags": missing,
                            "raw_tags": tags,
                        }

                        if missing:
                            region_result["non_compliant"].append(resource_record)
                        else:
                            region_result["compliant"].append(resource_record)

                logger.info("Region %s: %d total, %d compliant, %d non-compliant",
                           region, 
                           region_result["total"],
                           len(region_result["compliant"]),
                           len(region_result["non_compliant"]))
                
            except ClientError as e:
                error_msg = f"AWS API error in region {region}: {e}"
                logger.error(error_msg)
                region_result["errors"].append(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error in region {region}: {e}"
                logger.error(error_msg, exc_info=True)
                region_result["errors"].append(error_msg)

            account_result["regions"][region] = region_result

        results[account_id] = account_result

    logger.info("="*60)
    logger.info("Scan complete for %d accounts", len(results))
    logger.info("="*60)
    
    return results