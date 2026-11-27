"""A rendered CRD's CEL rules (`x-kubernetes-validations`), evaluated with cel-python.

The API server evaluates each rule with `self` bound to the value at the
schema node that declares it. This walks the CRD's schema and the object
together and does the same, for the rules it can:

- A rule that reads `oldSelf` is a transition rule. It runs only on an
  update, against the stored object, so it is skipped.
- A rule cel-python cannot parse, or that calls a Kubernetes CEL library
  function this module does not implement, is skipped and counted: there is
  nothing to judge with. Kubernetes' IP and CIDR libraries are implemented.

A rule that evaluates to `false` is a violation, and so is one that fails at
run time, as it is on the API server. Schema defaults are filled first, the
way the API server fills them, so a field it would default is not missing.
"""

from __future__ import annotations

import ipaddress
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, cast

import celpy
from celpy import celtypes
from celpy.adapter import json_to_cel
from celpy.celparser import CELParseError
from celpy.evaluation import CELEvalError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import lark
    from celpy.evaluation import CELFunction, Result

VALIDATIONS = "x-kubernetes-validations"

#: CEL keywords. A property with one of these names is `__name__` in a rule.
RESERVED = frozenset(
    {
        "true",
        "false",
        "null",
        "in",
        "as",
        "break",
        "const",
        "continue",
        "else",
        "for",
        "function",
        "if",
        "import",
        "let",
        "loop",
        "package",
        "namespace",
        "return",
        "var",
        "void",
        "while",
    }
)

_OLD_SELF = re.compile(r"\boldSelf\b")
_UNDECLARED = re.compile(r"undeclared reference to '([^']+)'")


def escape(name: str) -> str:
    """A property name as a CEL rule spells it, by Kubernetes' escaping rules.

    `namespace` is a CEL keyword, so a rule reads `self.__namespace__`.
    """
    if name in RESERVED:
        return f"__{name}__"
    return (
        name.replace("__", "__underscores__").replace(".", "__dot__").replace("-", "__dash__").replace("/", "__slash__")
    )


def cel_view(schema: Mapping[str, Any], value: Any) -> Any:
    """*value* as a rule sees it: defaults filled, declared properties renamed
    by `escape`.

    The API server applies a schema's defaults before it evaluates CEL, so a
    rule may read a field the manifest never set (Gateway API's
    `backendRefs[].kind`, defaulted to `Service`). Only declared properties
    are renamed: a map's keys (`additionalProperties`) are data, and a rule
    indexes them as `self['key']`, unescaped.
    """
    if isinstance(value, dict):
        properties = cast("Mapping[str, Any]", schema.get("properties") or {})
        defaults = {
            key: cast("Mapping[str, Any]", sub)["default"]
            for key, sub in properties.items()
            if isinstance(sub, dict) and "default" in sub
        }
        items = defaults | cast("dict[str, Any]", value)
        additional = schema.get("additionalProperties")
        out: dict[str, Any] = {}
        for key, child in items.items():
            if key in properties:
                out[escape(key)] = cel_view(cast("Mapping[str, Any]", properties[key]), child)
            elif isinstance(additional, dict):
                out[key] = cel_view(cast("Mapping[str, Any]", additional), child)
            else:
                out[key] = child
        return out
    if isinstance(value, list):
        sub = schema.get("items")
        sub_schema = cast("Mapping[str, Any]", sub) if isinstance(sub, dict) else {}
        return [cel_view(sub_schema, child) for child in value]
    return value


class _LazyMap(celtypes.MapType):
    """A CEL map that converts a value when an expression first reads it.

    `json_to_cel` of nixlab3's 201 CRDs took 0.62s, for policies that read
    three fields of each. celpy reads a map through `__getitem__` and `get`;
    the keys are real from the start, so `in`, `size` and the macros see them.

    A key named like a CEL keyword is also offered escaped, `__namespace__`
    beside `namespace`. Kubernetes escapes a declared property so, and with
    no schema for a built-in kind, which keys are properties is unknown.
    """

    __slots__ = ("_raw",)

    def __init__(self, raw: Mapping[str, Any]) -> None:
        super().__init__()
        self._raw = dict(raw)
        for key in RESERVED.intersection(raw):
            self._raw.setdefault(f"__{key}__", raw[key])
        # `None` holds each key's place until `__getitem__` converts its value.
        dict.update(self, dict.fromkeys(map(celtypes.StringType, self._raw)))  # type: ignore[arg-type]

    def __getitem__(self, key: Any) -> Any:
        value = super().__getitem__(key)
        if value is None and key in self._raw:
            value = lazy_cel(self._raw.pop(key))
            dict.__setitem__(self, key, value)
        return value

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self:
            return self[key]
        return super().get(key, default)


def lazy_cel(value: Any) -> Any:
    """`json_to_cel`, with each map a `_LazyMap`."""
    if isinstance(value, dict):
        return _LazyMap(cast("dict[str, Any]", value))
    if isinstance(value, list):
        return celtypes.ListType([lazy_cel(child) for child in value])
    return json_to_cel(value)


# Kubernetes' IP and CIDR CEL libraries: k8s.io/apiserver/pkg/cel/library/ip.go
# and cidr.go, over Go's net/netip. Python's `ipaddress` parses the same strings
# as `netip.ParseAddr` once a zone is ruled out: 350,000 fuzzed strings against
# Go 1.27 netip agreed on acceptance, String(), Masked() and every `is*` method
# below (2026-11-28). Python's own predicates are not Go's (`is_global` follows
# the IANA registry), so the `is*` methods follow netip.

type Address = ipaddress.IPv4Address | ipaddress.IPv6Address

_PREFIX_BITS = re.compile(r"0|[1-9][0-9]*")


@dataclass(frozen=True, slots=True)
class IP:
    """A CEL `net.IP`."""

    addr: Address

    def __str__(self) -> str:
        return str(self.addr)


@dataclass(frozen=True, slots=True)
class CIDR:
    """A CEL `net.CIDR`. Like `netip.Prefix`, it keeps the bits after the
    prefix: `cidr('192.168.0.1/24') != cidr('192.168.0.0/24')`."""

    addr: Address
    bits: int

    def __str__(self) -> str:
        return f"{self.addr}/{self.bits}"

    def network(self) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
        return ipaddress.ip_network((self.addr, self.bits), strict=False)


def parse_ip(text: str) -> Address:
    """Kubernetes' `parseIPAddr`: no zone, no IPv4-mapped IPv6 address, and
    no leading zero in an IPv4 octet."""
    if "%" in text:
        raise ValueError(f"IP address {text!r} with zone value is not allowed")
    try:
        addr = ipaddress.ip_address(text)
    except ValueError as exc:
        raise ValueError(f"IP Address {text!r} parse error during conversion from string: {exc}") from exc
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        raise ValueError(f"IPv4-mapped IPv6 address {text!r} is not allowed")
    return addr


def parse_cidr(text: str) -> CIDR:
    """Kubernetes' `parseCIDR`: `netip.ParsePrefix`, then no IPv4-mapped IPv6
    address. The prefix length is decimal with no sign and no leading zero."""
    address, slash, bits = text.rpartition("/")
    if not slash:
        raise ValueError(f"network address {text!r} has no '/'")
    addr = parse_ip(address)
    if not _PREFIX_BITS.fullmatch(bits) or int(bits) > addr.max_prefixlen:
        raise ValueError(f"network address {text!r} has a bad prefix length")
    return CIDR(addr, int(bits))


def _is_loopback(addr: Address) -> bool:
    if addr.version == 4:
        return addr.packed[0] == 127
    return int(addr) == 1


def _is_multicast(addr: Address) -> bool:
    if addr.version == 4:
        return addr.packed[0] & 0xF0 == 0xE0
    return addr.packed[0] == 0xFF


def _is_link_local_multicast(addr: Address) -> bool:
    if addr.version == 4:
        return addr.packed[:3] == b"\xe0\x00\x00"
    # ff02::/16 with any flags nibble: netip tests `v6u16(0)&0xff0f == 0xff02`.
    return int.from_bytes(addr.packed[:2]) & 0xFF0F == 0xFF02


def _is_link_local_unicast(addr: Address) -> bool:
    if addr.version == 4:
        return addr.packed[:2] == b"\xa9\xfe"
    return int.from_bytes(addr.packed[:2]) & 0xFFC0 == 0xFE80


def _is_global_unicast(addr: Address) -> bool:
    if int(addr) == 0 or (addr.version == 4 and int(addr) == 0xFFFFFFFF):
        return False
    return not (_is_loopback(addr) or _is_multicast(addr) or _is_link_local_unicast(addr))


def _error(exc: ValueError) -> CELEvalError:
    return CELEvalError(str(exc), type(exc), exc.args)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"no such overload for {type(value).__name__}")
    return str(value)


def _ip_of(value: object) -> IP:
    if not isinstance(value, IP):
        raise TypeError(f"no such overload for {type(value).__name__}")
    return value


def _cidr_of(value: object) -> CIDR:
    if not isinstance(value, CIDR):
        raise TypeError(f"no such overload for {type(value).__name__}")
    return value


def cel_ip(value: object) -> IP | CELEvalError:
    """`ip(<string>)`, and `<CIDR>.ip()`: the address, bits after the prefix kept."""
    if isinstance(value, CIDR):
        return IP(value.addr)
    try:
        return IP(parse_ip(_string(value)))
    except ValueError as exc:
        return _error(exc)


def cel_cidr(value: object) -> CIDR | CELEvalError:
    try:
        return parse_cidr(_string(value))
    except ValueError as exc:
        return _error(exc)


def cel_is_ip(value: object) -> celtypes.BoolType:
    try:
        parse_ip(_string(value))
    except ValueError:
        return celtypes.BoolType(False)
    return celtypes.BoolType(True)


def cel_is_cidr(value: object) -> celtypes.BoolType:
    try:
        parse_cidr(_string(value))
    except ValueError:
        return celtypes.BoolType(False)
    return celtypes.BoolType(True)


def cel_is_canonical(namespace: object, value: object) -> celtypes.BoolType | CELEvalError:
    """`ip.isCanonical(<string>)`. celpy reads `ip.` as a method call on the
    `ip` function, so *namespace* is that function."""
    if namespace is not cel_ip:
        raise TypeError("no such overload: isCanonical is ip.isCanonical")
    text = _string(value)
    try:
        return celtypes.BoolType(str(parse_ip(text)) == text)
    except ValueError as exc:
        return _error(exc)


def _ip_predicate(predicate: Callable[[Address], bool]) -> Callable[[object], celtypes.BoolType]:
    return lambda value: celtypes.BoolType(predicate(_ip_of(value).addr))


def cel_contains_ip(value: object, other: object) -> celtypes.BoolType | CELEvalError:
    """`<CIDR>.containsIP(<IP or string>)`. An address of the other family is not contained."""
    network = _cidr_of(value).network()
    found = other if isinstance(other, IP) else cel_ip(_string(other))
    if isinstance(found, CELEvalError):
        return found
    return celtypes.BoolType(found.addr.version == network.version and found.addr in network)


def cel_contains_cidr(value: object, other: object) -> celtypes.BoolType | CELEvalError:
    """`<CIDR>.containsCIDR(<CIDR or string>)`: *other* overlaps and is no wider."""
    outer = _cidr_of(value)
    inner = other if isinstance(other, CIDR) else cel_cidr(_string(other))
    if isinstance(inner, CELEvalError):
        return inner
    network = outer.network()
    return celtypes.BoolType(
        inner.addr.version == network.version and inner.addr in network and outer.bits <= inner.bits
    )


#: The IP and CIDR libraries, by CEL name. `string(<IP or CIDR>)` needs no
#: entry: celpy's `string` is `str`, which calls `__str__`.
KUBERNETES_FUNCTIONS: Final[dict[str, Callable[..., object]]] = {
    "ip": cel_ip,
    "isIP": cel_is_ip,
    "isCanonical": cel_is_canonical,
    "family": lambda value: celtypes.IntType(_ip_of(value).addr.version),
    "isUnspecified": _ip_predicate(lambda addr: int(addr) == 0),
    "isLoopback": _ip_predicate(_is_loopback),
    "isLinkLocalMulticast": _ip_predicate(_is_link_local_multicast),
    "isLinkLocalUnicast": _ip_predicate(_is_link_local_unicast),
    "isGlobalUnicast": _ip_predicate(_is_global_unicast),
    "cidr": cel_cidr,
    "isCIDR": cel_is_cidr,
    "containsIP": cel_contains_ip,
    "containsCIDR": cel_contains_cidr,
    "masked": lambda value: CIDR(_cidr_of(value).network().network_address, _cidr_of(value).bits),
    "prefixLength": lambda value: celtypes.IntType(_cidr_of(value).bits),
}

# The rest of the Kubernetes CEL environment (k8s.io/apiserver/pkg/cel/environment/base.go):
# its own libraries, and cel-go's strings v2, sets, lists v3, optionals and two-variable
# comprehensions. celpy has none of them, so a rule that calls one is skipped, not failed.

#: Global functions, called as `name(...)`.
UNIMPLEMENTED_FUNCTIONS: Final = frozenset({"quantity", "isQuantity", "url", "isURL", "semver", "isSemver"})

#: Methods, called as `x.name(...)`.
UNIMPLEMENTED_METHODS: Final = frozenset(
    {
        # lists
        "isSorted",
        "sum",
        "max",
        "indexOf",
        "lastIndexOf",
        # regex
        "find",
        "findAll",
        # urls
        "getScheme",
        "getHost",
        "getHostname",
        "getPort",
        "getEscapedPath",
        "getQuery",
        # quantity and semver
        "sign",
        "isGreaterThan",
        "isLessThan",
        "compareTo",
        "asApproximateFloat",
        "asInteger",
        "isInteger",
        "add",
        "sub",
        "major",
        "minor",
        "patch",
        # format
        "validate",
        # cel-go strings
        "charAt",
        "lowerAscii",
        "upperAscii",
        "replace",
        "split",
        "substring",
        "trim",
        "join",
        "format",
        # cel-go lists
        "slice",
        "flatten",
        "distinct",
        "reverse",
        "sort",
        "sortBy",
        "first",
        "last",
        # cel-go optionals
        "hasValue",
        "value",
        "orValue",
        "or",
        "optMap",
        "optFlatMap",
        # cel-go two-variable comprehensions
        "existsOne",
        "transformList",
        "transformMap",
        "transformMapEntry",
    }
)

#: Namespaces, called as `namespace.name(...)`.
UNIMPLEMENTED_NAMESPACES: Final = frozenset({"format", "jsonpatch", "lists", "optional", "sets", "strings"})

#: Macros celpy has in their one-variable form only. With two it raises.
_COMPREHENSIONS: Final = frozenset({"all", "exists", "exists_one"})


def _bare_ident(tree: lark.Tree[lark.Token] | lark.Token) -> str | None:
    """The identifier *tree* is, when it is nothing more: `sets` in `sets.contains(...)`."""
    node = tree
    while not isinstance(node, str) and node.data in {"member", "primary"} and len(node.children) == 1:
        node = node.children[0]
    if not isinstance(node, str) and node.data == "ident":
        return str(node.children[0])
    return None


def unimplemented_call(ast: lark.Tree[lark.Token]) -> str | None:
    """A call in *ast* to a Kubernetes CEL library function this module does
    not implement, innermost first. A lark `Token` is a `str`; every other
    node is a `Tree`."""
    for node in ast.iter_subtrees():
        if node.data == "ident_arg":
            name = str(node.children[0])
            if name in UNIMPLEMENTED_FUNCTIONS:
                return name
        elif node.data == "member_dot_arg":
            receiver, method = node.children[0], str(node.children[1])
            namespace = _bare_ident(receiver)
            if namespace in UNIMPLEMENTED_NAMESPACES:
                return f"{namespace}.{method}"
            if method in UNIMPLEMENTED_METHODS:
                return method
            arguments = node.children[2] if len(node.children) > 2 else None
            if method in _COMPREHENSIONS and not isinstance(arguments, str | None) and len(arguments.children) == 3:
                return f"{method} with two variables"
    return None


@dataclass(frozen=True, slots=True)
class CelFailure:
    #: JSON pointer into the object, `""` for the object itself.
    path: str
    message: str


@dataclass(slots=True)
class CelResult:
    evaluated: int = 0
    #: Rules not evaluated, by reason: "oldSelf" or "unsupported: <what>".
    skipped: Counter[str] = field(default_factory=Counter)
    failures: list[CelFailure] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Unsupported:
    """An expression with nothing to judge it by."""

    #: "unsupported: syntax", "unsupported: <the unimplemented library call>",
    #: or "unsupported: <the undeclared name>".
    reason: str


@dataclass(slots=True)
class CelChecker:
    _env: celpy.Environment = field(default_factory=celpy.Environment)
    #: Compiled once per rule text. A rule that does not compile maps to its
    #: skip reason.
    _programs: dict[str, celpy.Runner | str] = field(default_factory=dict)

    def _program(self, rule: str) -> celpy.Runner | str:
        found = self._programs.get(rule)
        if found is None:
            try:
                ast = self._env.compile(rule)
            except CELParseError:
                found = "unsupported: syntax"
            else:
                call = unimplemented_call(ast)
                # celpy's `CELFunction` returns its own value union, which has no room for `IP` or `CIDR`.
                functions = cast("dict[str, CELFunction]", KUBERNETES_FUNCTIONS)
                found = f"unsupported: {call}" if call else self._env.program(ast, functions)
            self._programs[rule] = found
        return found

    def run(self, text: str, activation: Mapping[str, Any]) -> Result | CELEvalError | Unsupported:
        """*text* over *activation*, whose values are CEL values: the result,
        the error it failed with, or why it cannot be judged."""
        program = self._program(text)
        if isinstance(program, str):
            return Unsupported(program)
        outcome: Result | CELEvalError
        try:
            outcome = program.evaluate(dict(activation))
        except CELEvalError as exc:
            outcome = exc
        if isinstance(outcome, CELEvalError):
            undeclared = _UNDECLARED.search(str(outcome))
            if undeclared:
                return Unsupported(f"unsupported: {undeclared[1]}")
        return outcome

    def _evaluate(
        self, rule: Mapping[str, Any], schema: Mapping[str, Any], value: Any, path: str, result: CelResult
    ) -> None:
        text = str(rule.get("rule", ""))
        if _OLD_SELF.search(text):
            result.skipped["oldSelf"] += 1
            return
        outcome = self.run(text, {"self": json_to_cel(cel_view(schema, value))})
        field_path = str(rule.get("fieldPath", "")).replace(".", "/")
        if isinstance(outcome, Unsupported):
            result.skipped[outcome.reason] += 1
            return
        if isinstance(outcome, CELEvalError):
            result.evaluated += 1
            result.failures.append(
                CelFailure(path + field_path, f"rule {text!r} failed to evaluate: {outcome.args[0]}")
            )
            return
        result.evaluated += 1
        if outcome == celtypes.BoolType(False):
            message = str(rule.get("message") or f"failed rule: {text}")
            result.failures.append(CelFailure(path + field_path, message))

    def walk(self, schema: Mapping[str, Any], value: Any, path: str, result: CelResult) -> None:
        """Every rule in *schema* against *value*, then the same for each child."""
        if value is None:
            return
        for rule in cast("list[Mapping[str, Any]]", schema.get(VALIDATIONS) or []):
            self._evaluate(rule, schema, value, path, result)
        if isinstance(value, dict):
            items = cast("dict[str, Any]", value)
            properties = cast("Mapping[str, Any]", schema.get("properties") or {})
            additional = schema.get("additionalProperties")
            for key, child in items.items():
                sub = properties.get(key, additional if isinstance(additional, dict) else None)
                if isinstance(sub, dict):
                    self.walk(cast("Mapping[str, Any]", sub), child, f"{path}/{key}", result)
        elif isinstance(value, list):
            sub = schema.get("items")
            if isinstance(sub, dict):
                for index, child in enumerate(value):
                    self.walk(cast("Mapping[str, Any]", sub), child, f"{path}/{index}", result)
