"""
Packet filter parameter validation utilities.
"""

import logging
import subprocess
from typing import Dict, List, Any, Optional

log = logging.getLogger(__name__)


class FilterValidationError(Exception):
    """Raised when packet filter parameters fail validation."""
    pass


def validate_bpf_syntax(bpf_expression: str) -> Dict[str, Optional[str]]:
    """
    Validate BPF filter expression syntax using tshark.

    This uses the same approach as gns3_copilot's packet filter tool:
    - Run tshark with the BPF expression on loopback interface
    - Check for "Invalid" in output indicating syntax errors
    - Timeout is expected behavior (tshark waits for traffic)

    Args:
        bpf_expression: BPF filter expression to validate

    Returns:
        dict with 'valid' (bool) and 'error' (str or None) keys
    """
    try:
        # Use tshark to validate BPF syntax with 1 second timeout
        # Use -i lo (loopback) to avoid "(null)" interface in error messages
        result = subprocess.run(
            ["tshark", "-f", bpf_expression, "-i", "lo"],
            timeout=1,
            capture_output=True,
            text=True,
        )

        # Check if output contains "Invalid" indicating syntax error
        if "Invalid" in result.stdout or "Invalid" in result.stderr:
            error_lines = []
            if "Invalid" in result.stderr:
                error_lines.extend(
                    line for line in result.stderr.split("\n") if "Invalid" in line
                )
            if "Invalid" in result.stdout:
                error_lines.extend(
                    line for line in result.stdout.split("\n") if "Invalid" in line
                )

            # Strip interface suffix for cleaner error
            error_msg_parts = []
            for line in error_lines:
                clean = line.split(" for interface")[0].strip()
                if clean:
                    error_msg_parts.append(clean)
            error_msg = " ".join(error_msg_parts) if error_msg_parts else "Invalid BPF syntax"
            log.warning("BPF syntax validation failed: %s", error_msg)
            return {"valid": False, "error": error_msg}

        log.info("BPF syntax validation passed")
        return {"valid": True, "error": None}

    except subprocess.TimeoutExpired:
        # Timeout is expected behavior - tshark waits for traffic
        # No "Invalid" in output means syntax is correct
        log.info("BPF syntax validation passed (timeout expected)")
        return {"valid": True, "error": None}

    except FileNotFoundError:
        # tshark not installed - skip validation
        log.warning(
            "tshark not found, skipping BPF syntax validation. "
            "Install tshark to enable BPF validation."
        )
        return {"valid": True, "error": None}

    except Exception as e:
        log.error("Unexpected error during BPF validation: %s", e)
        return {"valid": False, "error": f"BPF validation error: {str(e)}"}


def validate_filter_parameters(filter_type: str, values: List[Any]) -> None:
    """
    Validate packet filter parameters.

    Args:
        filter_type: Type of packet filter
        values: List of parameter values

    Raises:
        FilterValidationError: If parameters are invalid
    """

    # Define validation rules based on ubridge implementation
    VALIDATION_RULES = {
        "frequency_drop": {
            "params_count": 1,
            "ranges": [(-1, 32767)],  # min, max
            "names": ["Frequency"],
            "units": ["th packet"]
        },
        "packet_loss": {
            "params_count": 1,
            "ranges": [(0, 100)],
            "names": ["Chance"],
            "units": ["%"]
        },
        "delay": {
            "params_count": 2,  # latency, jitter
            "ranges": [(0, 32767), (0, 32767)],
            "names": ["Latency", "Jitter"],
            "units": ["ms", "ms"]
        },
        "corrupt": {
            "params_count": 1,
            "ranges": [(0, 100)],
            "names": ["Chance"],
            "units": ["%"]
        },
        "bpf": {
            "params_count": 1,
            "is_text": True,
            "names": ["Filters"]
        }
    }

    if filter_type not in VALIDATION_RULES:
        raise FilterValidationError(f"Unknown filter type: {filter_type}")

    rules = VALIDATION_RULES[filter_type]

    # Check parameter count
    if len(values) != rules["params_count"]:
        raise FilterValidationError(
            f"{filter_type} expects {rules['params_count']} parameter(s), got {len(values)}"
        )

    # Validate each parameter
    for i, value in enumerate(values):
        if rules.get("is_text"):
            # Text validation (BPF)
            if not isinstance(value, str):
                raise FilterValidationError(
                    f"{filter_type} parameter {rules['names'][i]} must be a string"
                )

            # Validate BPF syntax using tshark (same method as gns3_copilot)
            value = value.strip()
            if value:  # Only validate non-empty BPF expressions
                bpf_result = validate_bpf_syntax(value)
                if not bpf_result["valid"]:
                    raise FilterValidationError(
                        f"{filter_type} parameter {rules['names'][i]} has invalid syntax: {bpf_result['error']}"
                    )
        else:
            # Integer parameter validation
            try:
                if isinstance(value, str):
                    value = value.strip()
                    int_value = int(value)
                else:
                    int_value = int(value)
            except (ValueError, TypeError):
                raise FilterValidationError(
                    f"{filter_type} parameter {rules['names'][i]} must be an integer, got: {value}"
                )

            # Range validation
            min_val, max_val = rules["ranges"][i]
            if int_value < min_val or int_value > max_val:
                raise FilterValidationError(
                    f"{filter_type} parameter {rules['names'][i]} must be between "
                    f"{min_val} and {max_val} {rules['units'][i]}, got: {int_value}"
                )


def validate_all_filters(filters: Dict[str, List[Any]]) -> None:
    """
    Validate all packet filters.

    Args:
        filters: Dictionary mapping filter types to their values

    Raises:
        FilterValidationError: If any filter is invalid
    """

    if not filters:
        return

    for filter_type, values in filters.items():
        if not values or (isinstance(values, list) and len(values) == 0):
            continue

        validate_filter_parameters(filter_type, values)