from __future__ import annotations

from datetime import date

import pytest
import yaml

from ekn.manifestyaml import dump_manifest
from ekn.nixyaml import KubernetesDumper, from_go_like_yaml


@pytest.mark.parametrize(
    "value",
    [
        {},
        [],
        {"z": [], "a": {}, "b": [[], {}, None, True, False]},
        {"strings": ["y", "n", "1e+06", "08", "<<", "=", "", "yes", "null", "2026-10-14"]},
        {"text": "line\nline\n", "emoji": "😀", "nul": "nul\0end"},
        {"text": 'long "quoted" ' * 30},
        {"y": "Y", "n": "N", "1e+06": "1e+06"},
        {"numbers": [-(2**63), 2**63 - 1, 1.0, 1e20, 1e-20, -0.0]},
    ],
)
def test_matches_kubernetes_dumper_bytes(value: object) -> None:
    expected = yaml.dump(value, Dumper=KubernetesDumper, sort_keys=False, default_flow_style=False)
    assert dump_manifest(value) == expected
    assert from_go_like_yaml(expected) == value


def test_raw_yaml_aliases_keep_their_anchors() -> None:
    shared = {"value": "y"}
    value = {"first": shared, "second": shared}
    expected = yaml.dump(value, Dumper=KubernetesDumper, sort_keys=False, default_flow_style=False)
    assert dump_manifest(value) == expected
    assert "&id001" in expected
    assert "*id001" in expected


def test_raw_yaml_cycles_keep_their_anchors() -> None:
    value = {}
    value["cycle"] = value
    expected = yaml.dump(value, Dumper=KubernetesDumper, sort_keys=False, default_flow_style=False)
    assert dump_manifest(value) == expected


def test_raw_yaml_timestamp_keeps_its_format() -> None:
    value = {"date": date(2026, 10, 14)}
    expected = yaml.dump(value, Dumper=KubernetesDumper, sort_keys=False, default_flow_style=False)
    assert dump_manifest(value) == expected
