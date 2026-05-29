"""
Unit tests for packet filter validation.
"""

import pytest
from gns3server.utils.packet_filter_validation import (
    validate_filter_parameters,
    validate_all_filters,
    FilterValidationError
)


class TestPacketFilterValidation:
    """Test packet filter parameter validation."""

    def test_frequency_drop_valid(self):
        """Test valid frequency drop parameters."""
        # Valid range: -1 to 32767
        validate_filter_parameters("frequency_drop", [-1])
        validate_filter_parameters("frequency_drop", [1])
        validate_filter_parameters("frequency_drop", [100])
        validate_filter_parameters("frequency_drop", [32767])

    def test_frequency_drop_invalid(self):
        """Test invalid frequency drop parameters."""
        # Too low
        with pytest.raises(FilterValidationError, match="between -1 and 32767"):
            validate_filter_parameters("frequency_drop", [-2])

        # Too high
        with pytest.raises(FilterValidationError, match="between -1 and 32767"):
            validate_filter_parameters("frequency_drop", [32768])

        # Wrong type
        with pytest.raises(FilterValidationError, match="must be an integer"):
            validate_filter_parameters("frequency_drop", ["invalid"])

    def test_packet_loss_valid(self):
        """Test valid packet loss parameters."""
        # Valid range: 0-100%
        validate_filter_parameters("packet_loss", [0])
        validate_filter_parameters("packet_loss", [50])
        validate_filter_parameters("packet_loss", [100])

    def test_packet_loss_invalid(self):
        """Test invalid packet loss parameters."""
        # Negative
        with pytest.raises(FilterValidationError, match="between 0 and 100"):
            validate_filter_parameters("packet_loss", [-1])

        # Over 100%
        with pytest.raises(FilterValidationError, match="between 0 and 100"):
            validate_filter_parameters("packet_loss", [101])

    def test_delay_valid(self):
        """Test valid delay parameters."""
        # Valid range: 0-32767ms
        validate_filter_parameters("delay", [0, 0])
        validate_filter_parameters("delay", [100, 50])
        validate_filter_parameters("delay", [32767, 32767])

    def test_delay_invalid(self):
        """Test invalid delay parameters."""
        # Negative latency
        with pytest.raises(FilterValidationError, match="between 0 and 32767"):
            validate_filter_parameters("delay", [-1, 0])

        # Over max
        with pytest.raises(FilterValidationError, match="between 0 and 32767"):
            validate_filter_parameters("delay", [32768, 0])

        # Negative jitter
        with pytest.raises(FilterValidationError, match="between 0 and 32767"):
            validate_filter_parameters("delay", [100, -1])

    def test_corrupt_valid(self):
        """Test valid corrupt parameters."""
        # Valid range: 0-100%
        validate_filter_parameters("corrupt", [0])
        validate_filter_parameters("corrupt", [50])
        validate_filter_parameters("corrupt", [100])

    def test_corrupt_invalid(self):
        """Test invalid corrupt parameters."""
        # Over 100%
        with pytest.raises(FilterValidationError, match="between 0 and 100"):
            validate_filter_parameters("corrupt", [101])

    def test_bpf_valid(self):
        """Test valid BPF parameters."""
        validate_filter_parameters("bpf", ["tcp port 80"])
        validate_filter_parameters("bpf", ["tcp and not port 22"])
        validate_filter_parameters("bpf", [""])  # Empty is valid
        validate_filter_parameters("bpf", ["host 192.168.1.1 and port 443"])

    def test_bpf_multi_line_valid(self):
        """Test valid multi-line BPF expressions."""
        validate_filter_parameters("bpf", ["tcp port 80\nnot arp"])
        validate_filter_parameters("bpf", ["tcp and not port 22\nhost 192.168.1.1\nicmp"])

    def test_bpf_multi_line_invalid(self):
        """Test multi-line BPF with invalid line."""
        with pytest.raises(FilterValidationError) as excinfo:
            validate_filter_parameters("bpf", ["tcp port 80\ninvalid!!!"])
        err = str(excinfo.value).lower()
        assert "syntax error" in err

    def test_bpf_invalid(self):
        """Test invalid BPF parameters."""
        # Wrong type
        with pytest.raises(FilterValidationError, match="must be a string"):
            validate_filter_parameters("bpf", [123])

        # Invalid BPF syntax
        with pytest.raises(FilterValidationError) as excinfo:
            validate_filter_parameters("bpf", ["tcp port"])  # Missing port number
        assert "syntax error" in str(excinfo.value).lower()

    def test_parameter_count_mismatch(self):
        """Test wrong number of parameters."""
        # frequency_drop expects 1 parameter
        with pytest.raises(FilterValidationError, match="expects 1 parameter"):
            validate_filter_parameters("frequency_drop", [])

        with pytest.raises(FilterValidationError, match="expects 1 parameter"):
            validate_filter_parameters("frequency_drop", [1, 2])

        # delay expects 2 parameters
        with pytest.raises(FilterValidationError, match="expects 2 parameter"):
            validate_filter_parameters("delay", [100])

    def test_string_to_int_conversion(self):
        """Test string to integer conversion."""
        # Should work with string numbers
        validate_filter_parameters("frequency_drop", ["10"])
        validate_filter_parameters("packet_loss", ["50"])
        validate_filter_parameters("delay", ["100", "50"])

    def test_validate_all_filters(self):
        """Test validating multiple filters at once."""
        filters = {
            "frequency_drop": [10],
            "delay": [100, 50]
        }
        validate_all_filters(filters)  # Should not raise

    def test_validate_all_filters_with_invalid(self):
        """Test validate_all_filters with invalid filter."""
        filters = {
            "frequency_drop": [10],
            "packet_loss": [150]  # Invalid: over 100%
        }
        with pytest.raises(FilterValidationError):
            validate_all_filters(filters)

    def test_unknown_filter_type(self):
        """Test unknown filter type."""
        with pytest.raises(FilterValidationError, match="Unknown filter type"):
            validate_filter_parameters("unknown_filter", [1])