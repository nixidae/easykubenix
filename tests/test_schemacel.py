"""Kubernetes' IP and CIDR CEL libraries in `ekn.schemacel`.

The cases are Kubernetes' own: the doc comments and tests of
k8s.io/apiserver/pkg/cel/library/ip.go and cidr.go.
"""

from __future__ import annotations

import pytest
from celpy import celtypes
from celpy.evaluation import CELEvalError

from ekn.schemacel import CelChecker, Unsupported

TRUE = celtypes.BoolType(True)
FALSE = celtypes.BoolType(False)


def run(rule: str) -> object:
    return CelChecker().run(rule, {})


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("isIP('127.0.0.1')", TRUE),
        ("isIP('::1')", TRUE),
        ("isIP('2001:db8::68')", TRUE),
        ("isIP('127.0.0.256')", FALSE),
        ("isIP(':::1')", FALSE),
        ("isIP('192.168.0.1.0')", FALSE),
        ("isIP('2001:db8:::68')", FALSE),
        ("isIP('::ffff:1.2.3.4')", FALSE),
        ("isIP('::ffff:0102:0304')", FALSE),
        ("isIP('fe80::1%eth0')", FALSE),
        ("isIP('01.2.3.4')", FALSE),
        ("isIP('1.2.3.04')", FALSE),
        ("isIP('::1.2.3.4')", TRUE),
        ("isIP('')", FALSE),
        ("isIP('192.168.0.0/24')", FALSE),
    ],
)
def test_is_ip(rule: str, expected: celtypes.BoolType) -> None:
    assert run(rule) == expected


@pytest.mark.parametrize(
    "rule",
    ["ip('127.0.0.256')", "ip(':::1')", "ip('::ffff:1.2.3.4')", "ip('fe80::1%eth0')", "ip('01.2.3.4')"],
)
def test_ip_rejects_what_is_ip_rejects(rule: str) -> None:
    assert isinstance(run(rule), CELEvalError)


def test_ip_rejects_a_non_string() -> None:
    outcome = run("ip(1)")
    assert isinstance(outcome, CELEvalError)
    assert outcome.args[0] == "no such overload"


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("ip.isCanonical('127.0.0.1')", TRUE),
        ("ip.isCanonical('2001:db8::abcd')", TRUE),
        ("ip.isCanonical('2001:DB8::ABCD')", FALSE),
        ("ip.isCanonical('2001:db8::0:0:0:abcd')", FALSE),
        ("ip('192.168.0.1').family() == 4", TRUE),
        ("ip('2001:db8::68').family() == 6", TRUE),
        ("ip('0.0.0.0').isUnspecified()", TRUE),
        ("ip('127.0.0.1').isUnspecified()", FALSE),
        ("ip('::').isUnspecified()", TRUE),
        ("ip('::1').isUnspecified()", FALSE),
        ("ip('127.0.0.1').isLoopback()", TRUE),
        ("ip('127.255.0.1').isLoopback()", TRUE),
        ("ip('192.168.0.1').isLoopback()", FALSE),
        ("ip('::1').isLoopback()", TRUE),
        ("ip('2001:db8::abcd').isLoopback()", FALSE),
        ("ip('224.0.0.1').isLinkLocalMulticast()", TRUE),
        ("ip('224.0.1.1').isLinkLocalMulticast()", FALSE),
        ("ip('ff02::1').isLinkLocalMulticast()", TRUE),
        ("ip('ff12::1').isLinkLocalMulticast()", TRUE),
        ("ip('ff05::1').isLinkLocalMulticast()", FALSE),
        ("ip('fd00::1').isLinkLocalMulticast()", FALSE),
        ("ip('169.254.169.254').isLinkLocalUnicast()", TRUE),
        ("ip('192.168.0.1').isLinkLocalUnicast()", FALSE),
        ("ip('fe80::1').isLinkLocalUnicast()", TRUE),
        ("ip('febf::1').isLinkLocalUnicast()", TRUE),
        ("ip('fec0::1').isLinkLocalUnicast()", FALSE),
        ("ip('fd80::1').isLinkLocalUnicast()", FALSE),
        ("ip('192.168.0.1').isGlobalUnicast()", TRUE),
        ("ip('10.0.0.1').isGlobalUnicast()", TRUE),
        ("ip('255.255.255.255').isGlobalUnicast()", FALSE),
        ("ip('0.0.0.0').isGlobalUnicast()", FALSE),
        ("ip('224.0.0.1').isGlobalUnicast()", FALSE),
        ("ip('2001:db8::abcd').isGlobalUnicast()", TRUE),
        ("ip('fd00::1').isGlobalUnicast()", TRUE),
        ("ip('ff00::1').isGlobalUnicast()", FALSE),
        ("ip('fe80::1').isGlobalUnicast()", FALSE),
        ("ip('::1').isGlobalUnicast()", FALSE),
        ("ip('192.168.0.1') == ip('192.168.0.1')", TRUE),
        ("ip('2001:db8::1') == ip('2001:DB8:0::1')", TRUE),
        ("ip('192.168.0.1') == ip('192.168.0.2')", FALSE),
        ("string(ip('192.168.0.1')) == '192.168.0.1'", TRUE),
        ("string(ip('2001:DB8:0:0::68')) == '2001:db8::68'", TRUE),
    ],
)
def test_ip_methods(rule: str, expected: celtypes.BoolType) -> None:
    assert run(rule) == expected


def test_is_canonical_rejects_a_bad_address() -> None:
    assert isinstance(run("ip.isCanonical('127.0.0.1.0')"), CELEvalError)


def test_an_ip_method_rejects_a_string() -> None:
    assert isinstance(run("'127.0.0.1'.isLoopback()"), CELEvalError)


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("isCIDR('192.168.0.0/16')", TRUE),
        ("isCIDR('::1/128')", TRUE),
        ("isCIDR('192.168.0.1/16')", TRUE),
        ("isCIDR('0.0.0.0/0')", TRUE),
        ("isCIDR('192.168.0.0/33')", FALSE),
        ("isCIDR('::1/129')", FALSE),
        ("isCIDR('192.168.0.0/')", FALSE),
        ("isCIDR('192.168.0.0')", FALSE),
        ("isCIDR('192.168.0.0/024')", FALSE),
        ("isCIDR('192.168.0.0/+8')", FALSE),
        ("isCIDR('192.168.0.0/255.255.0.0')", FALSE),
        ("isCIDR('::ffff:1.2.3.4/120')", FALSE),
        ("isCIDR('fe80::%eth0/64')", FALSE),
        ("isCIDR('01.2.3.0/24')", FALSE),
        ("isCIDR('not-a-cidr')", FALSE),
    ],
)
def test_is_cidr(rule: str, expected: celtypes.BoolType) -> None:
    assert run(rule) == expected


@pytest.mark.parametrize("rule", ["cidr('192.168.0.0/')", "cidr('2001:db8::/')", "cidr('::1/129')"])
def test_cidr_rejects_what_is_cidr_rejects(rule: str) -> None:
    assert isinstance(run(rule), CELEvalError)


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("cidr('192.168.0.0/24').containsIP(ip('192.168.0.1'))", TRUE),
        ("cidr('192.168.0.0/24').containsIP(ip('192.168.1.1'))", FALSE),
        ("cidr('192.168.0.0/24').containsIP('192.168.0.1')", TRUE),
        ("cidr('192.168.0.0/24').containsIP('192.168.1.1')", FALSE),
        ("cidr('2001:db8::/32').containsIP(ip('2001:db8::1'))", TRUE),
        ("cidr('2001:db8::/32').containsIP('2001:dc8::1')", FALSE),
        ("cidr('0.0.0.0/0').containsIP('::1')", FALSE),
        ("cidr('192.168.0.0/24').containsCIDR(cidr('192.168.0.0/25'))", TRUE),
        ("cidr('192.168.0.0/24').containsCIDR(cidr('192.168.0.1/32'))", TRUE),
        ("cidr('192.168.0.0/24').containsCIDR(cidr('192.168.0.0/23'))", FALSE),
        ("cidr('192.168.0.0/24').containsCIDR(cidr('192.169.0.1/32'))", FALSE),
        ("cidr('192.168.0.0/24').containsCIDR('192.168.0.0/25')", TRUE),
        ("cidr('192.168.0.0/24').containsCIDR('192.168.0.0/23')", FALSE),
        ("cidr('2001:db8::/32').containsCIDR(cidr('2001:db8::/33'))", TRUE),
        ("cidr('2001:db8::/32').containsCIDR('2001:db8::/31')", FALSE),
        ("cidr('::/0').containsCIDR('10.0.0.0/8')", FALSE),
        ("cidr('192.168.0.1/24').ip() == ip('192.168.0.1')", TRUE),
        ("cidr('::1/128').ip().family() == 6", TRUE),
        ("cidr('192.168.0.1/24').masked() == cidr('192.168.0.0/24')", TRUE),
        ("cidr('2001:db8:1::/32').masked() == cidr('2001:db8::/32')", TRUE),
        ("cidr('192.168.0.0/24') == cidr('192.168.0.0/24').masked()", TRUE),
        ("cidr('192.168.0.1/24') == cidr('192.168.0.1/24').masked()", FALSE),
        ("cidr('192.168.0.0/16').prefixLength() == 16", TRUE),
        ("cidr('::1/128').prefixLength() == 128", TRUE),
        ("string(cidr('192.168.0.1/24')) == '192.168.0.1/24'", TRUE),
        ("string(cidr('2001:DB8::/32')) == '2001:db8::/32'", TRUE),
    ],
)
def test_cidr_methods(rule: str, expected: celtypes.BoolType) -> None:
    assert run(rule) == expected


@pytest.mark.parametrize(
    "rule",
    ["cidr('192.168.0.0/24').containsIP('not-an-ip')", "cidr('192.168.0.0/24').containsCIDR('192.168.0.0/33')"],
)
def test_contains_rejects_a_bad_string(rule: str) -> None:
    assert isinstance(run(rule), CELEvalError)


@pytest.mark.parametrize(
    ("rule", "reason"),
    [
        ("quantity(self).isGreaterThan(quantity('1'))", "unsupported: quantity"),
        ("isQuantity(self)", "unsupported: isQuantity"),
        ("url(self).getHost() == 'a'", "unsupported: url"),
        ("self.getHost() == 'a'", "unsupported: getHost"),
        ("self.find('[0-9]+') == '1'", "unsupported: find"),
        ("'%s'.format([self]) == 'a'", "unsupported: format"),
        ("format.dns1123Label().validate(self).hasValue()", "unsupported: format.dns1123Label"),
        ("self.validate('a').hasValue()", "unsupported: validate"),
        ("sets.contains([1], [1])", "unsupported: sets.contains"),
        ("[1].all(i, isSemver(string(i)))", "unsupported: isSemver"),
        ("{'a': 1}.all(k, v, v > 0)", "unsupported: all with two variables"),
    ],
)
def test_an_unimplemented_library_call_is_unsupported(rule: str, reason: str) -> None:
    assert run(rule) == Unsupported(reason)


def test_a_real_error_in_an_implemented_call_is_an_error() -> None:
    assert isinstance(run("['a'].all(c, cidr(c).prefixLength() > 0)"), CELEvalError)
