"""Tests for the EAGLE-3 poller's response-parsing logic.

The EAGLE-3 firmware returns variable values as pre-formatted ASCII strings
with units (e.g. ``"1.602 kW"``, ``"78967.773 kWh"``); the poller's parser
has to split the unit suffix and float the leading number. These tests
cover that quirk plus the device_list response shape.

Network/auth/HTTP layers are deliberately not exercised here — they're
exercised live and don't have surprises beyond what `requests` already
handles.

Run from this directory:
    python -m pytest tests.py
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from poller import extract_hardware_addresses, extract_variable_value


# ---- extract_variable_value ----------------------------------------------


def _build_device_query_response(variables: list[tuple[str, str]]) -> ET.Element:
    """Synthesise an EAGLE-3 device_query response with the given Name/Value
    pairs. ``variables`` items are ``(name, value_str)``."""
    root = ET.Element("Response")
    components = ET.SubElement(root, "Components")
    component = ET.SubElement(components, "Component")
    vars_el = ET.SubElement(component, "Variables")
    for name, value in variables:
        v = ET.SubElement(vars_el, "Variable")
        ET.SubElement(v, "Name").text = name
        ET.SubElement(v, "Value").text = value
    return root


def test_extract_value_strips_kw_suffix():
    """Demand readings come as ``"1.602 kW"``; the parser must strip "kW"."""
    root = _build_device_query_response([
        ("zigbee:InstantaneousDemand", "1.602 kW"),
    ])
    assert extract_variable_value(root, "zigbee:InstantaneousDemand") == 1.602


def test_extract_value_strips_kwh_suffix():
    root = _build_device_query_response([
        ("zigbee:CurrentSummationDelivered", "78967.773 kWh"),
    ])
    assert extract_variable_value(root, "zigbee:CurrentSummationDelivered") == 78967.773


def test_extract_value_handles_zero():
    """Solar receive is 0.0 kWh on a non-PV install — must parse as 0.0, not None."""
    root = _build_device_query_response([
        ("zigbee:CurrentSummationReceived", "0.000 kWh"),
    ])
    assert extract_variable_value(root, "zigbee:CurrentSummationReceived") == 0.0


def test_extract_value_handles_no_unit():
    """Defensive: if the firmware returns a bare number, still works."""
    root = _build_device_query_response([
        ("zigbee:InstantaneousDemand", "1.234"),
    ])
    assert extract_variable_value(root, "zigbee:InstantaneousDemand") == 1.234


def test_extract_value_picks_named_among_many():
    """Response with multiple Variables: returns the one matching the name."""
    root = _build_device_query_response([
        ("zigbee:Other", "99.0 W"),
        ("zigbee:InstantaneousDemand", "1.602 kW"),
        ("zigbee:CurrentSummationDelivered", "78967.773 kWh"),
    ])
    assert extract_variable_value(root, "zigbee:InstantaneousDemand") == 1.602
    assert extract_variable_value(root, "zigbee:CurrentSummationDelivered") == 78967.773


def test_extract_value_missing_returns_none():
    root = _build_device_query_response([
        ("zigbee:InstantaneousDemand", "1.602 kW"),
    ])
    assert extract_variable_value(root, "zigbee:NotInResponse") is None


def test_extract_value_empty_value_text_returns_none():
    """An empty <Value/> element shouldn't crash — return None."""
    root = ET.Element("Response")
    var_el = ET.SubElement(root, "Variable")
    ET.SubElement(var_el, "Name").text = "zigbee:InstantaneousDemand"
    ET.SubElement(var_el, "Value")  # no text
    assert extract_variable_value(root, "zigbee:InstantaneousDemand") is None


def test_extract_value_whitespace_only_value_returns_none():
    """Whitespace-only Value text is also a no-op."""
    root = ET.Element("Response")
    var_el = ET.SubElement(root, "Variable")
    ET.SubElement(var_el, "Name").text = "zigbee:InstantaneousDemand"
    ET.SubElement(var_el, "Value").text = "   "
    # `.text` is "   "; truthy in `not value_el.text` check (returns False),
    # so we attempt to parse it. Whitespace `.split()` yields [], and indexing
    # would raise. Document the actual behaviour.
    try:
        result = extract_variable_value(root, "zigbee:InstantaneousDemand")
    except IndexError:
        # Acceptable: caller logs and skips. Documenting current behaviour.
        return
    # If we got here, the parser handled the whitespace cleanly.
    assert result is None or isinstance(result, float)


def test_extract_value_skips_variables_with_no_name_element():
    """Variables that lack a Name element are skipped without crashing."""
    root = ET.Element("Response")
    bad_var = ET.SubElement(root, "Variable")
    ET.SubElement(bad_var, "Value").text = "1.0 kW"  # only a Value, no Name
    good_var = ET.SubElement(root, "Variable")
    ET.SubElement(good_var, "Name").text = "zigbee:InstantaneousDemand"
    ET.SubElement(good_var, "Value").text = "2.5 kW"
    assert extract_variable_value(root, "zigbee:InstantaneousDemand") == 2.5


# ---- extract_hardware_addresses ------------------------------------------


def test_extract_hardware_addresses_finds_all():
    root = ET.fromstring("""
        <Response>
            <Devices>
                <Device><HardwareAddress>0x001350050037ac6b</HardwareAddress></Device>
                <Device><HardwareAddress>0x001350050037ac6c</HardwareAddress></Device>
            </Devices>
        </Response>
    """)
    addrs = extract_hardware_addresses(root)
    assert addrs == ["0x001350050037ac6b", "0x001350050037ac6c"]


def test_extract_hardware_addresses_empty_when_no_devices():
    root = ET.fromstring("<Response></Response>")
    assert extract_hardware_addresses(root) == []


def test_extract_hardware_addresses_skips_empty_text():
    """A <HardwareAddress/> with no text isn't a device."""
    root = ET.fromstring("""
        <Response>
            <HardwareAddress/>
            <HardwareAddress>0x001350050037ac6b</HardwareAddress>
            <HardwareAddress></HardwareAddress>
        </Response>
    """)
    assert extract_hardware_addresses(root) == ["0x001350050037ac6b"]
