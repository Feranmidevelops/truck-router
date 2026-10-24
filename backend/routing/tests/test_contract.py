"""The API responses against the types the frontend declares in frontend/src/api/types.ts.

The interfaces are read from the file itself, so a field added on either side without the
other fails here. The checker knows the handful of TypeScript forms types.ts uses and fails
loudly on anything else.
"""

import copy
from pathlib import Path
import re

from django.conf import settings
from django.test import override_settings
from rest_framework.test import APIClient
import pytest

from routing.services import planner, tomtom
from routing.tests.fakes import install_service_fakes, plan, wiggly_route
from routing.views import load_fixture

TYPES_TS = Path(settings.BASE_DIR).parent / "frontend" / "src" / "api" / "types.ts"


def parse_types(source: str) -> tuple[dict[str, dict[str, tuple[str, bool]]], dict[str, str]]:
    """({interface: {field: (type, optional)}}, {alias: type}) from a .ts file."""
    aliases = dict(re.findall(r"^export type (\w+) = (.+?)\s*$", source, re.M))
    interfaces: dict[str, dict[str, tuple[str, bool]]] = {}
    declarations = re.findall(r"^export interface (\w+)(?: extends (\w+))? \{(.*?)^\}", source, re.M | re.S)
    for name, parent, body in declarations:
        fields = dict(interfaces[parent]) if parent else {}
        for line in body.splitlines():
            line = re.sub(r"//.*", "", line).strip()
            if not line:
                continue
            m = re.fullmatch(r"(\w+)(\??): (.+?);?", line)
            assert m, f"types.ts: can't read the field {line!r} in {name}"
            fields[m[1]] = (m[3], m[2] == "?")
        interfaces[name] = fields
    return interfaces, aliases


def split_top_level(text: str, sep: str) -> list[str]:
    """Split on `sep`, but not inside <...>, [...] or (...)."""
    parts, depth, current = [], 0, ""
    for ch in text:
        if ch in "<[(":
            depth += 1
        elif ch in ">])":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    return [*parts, current.strip()]


class Contract:
    def __init__(self, source: str):
        self.interfaces, self.aliases = parse_types(source)

    def errors(self, value, type_: str, path: str = "$") -> list[str]:
        t = type_.strip()
        alternatives = split_top_level(t, "|")
        if len(alternatives) > 1:
            if any(not self.errors(value, alt, path) for alt in alternatives):
                return []
            return [f"{path}: {value!r} is not {t}"]
        if t in self.aliases:
            return self.errors(value, self.aliases[t], path)
        if t in self.interfaces:
            return self._object(value, t, path)
        if re.fullmatch(r"'[^']*'", t):
            return [] if value == t[1:-1] else [f"{path}: {value!r} is not {t}"]
        if re.fullmatch(r"-?\d+(\.\d+)?", t):
            return [] if _is_number(value) and value == float(t) else [f"{path}: {value!r} is not {t}"]
        if t in PRIMITIVES:
            return [] if PRIMITIVES[t](value) else [f"{path}: {value!r} is not a {t}"]
        if m := re.fullmatch(r"(?:Array<(.+)>|(.+)\[\])", t):
            inner = m[1] or m[2]
            if not isinstance(value, list):
                return [f"{path}: expected an array"]
            return [e for i, item in enumerate(value) for e in self.errors(item, inner, f"{path}[{i}]")]
        if t.startswith("[") and t.endswith("]"):
            items = split_top_level(t[1:-1], ",")
            if not isinstance(value, list) or len(value) != len(items):
                return [f"{path}: expected a {len(items)}-item array"]
            pairs = enumerate(zip(value, items))
            return [e for i, (v, item) in pairs for e in self.errors(v, item, f"{path}[{i}]")]
        if m := re.fullmatch(r"Record<(.+)>", t):
            key_type, value_type = split_top_level(m[1], ",")
            if not isinstance(value, dict):
                return [f"{path}: expected an object"]
            keys = self._literals(key_type)
            errors = [] if set(value) == keys else [f"{path}: keys {sorted(value)} are not {sorted(keys)}"]
            return errors + [e for k, v in value.items() for e in self.errors(v, value_type, f"{path}.{k}")]
        raise AssertionError(f"types.ts uses a type this test can't check yet: {t}")

    def _object(self, value, name: str, path: str) -> list[str]:
        if not isinstance(value, dict):
            return [f"{path}: expected a {name} object"]
        fields = self.interfaces[name]
        errors = [f"{path}.{k}: sent but not declared in {name}" for k in value if k not in fields]
        for key, (type_, optional) in fields.items():
            if key in value:
                errors += self.errors(value[key], type_, f"{path}.{key}")
            elif not optional:
                errors.append(f"{path}.{key}: missing ({name} declares it)")
        return errors

    def _literals(self, type_: str) -> set[str]:
        type_ = self.aliases.get(type_.strip(), type_)
        return {alt[1:-1] for alt in split_top_level(type_, "|")}


def _is_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


PRIMITIVES = {
    "string": lambda v: isinstance(v, str),
    "number": _is_number,
    "boolean": lambda v: isinstance(v, bool),
}


@pytest.fixture(scope="module")
def contract():
    if not TYPES_TS.exists():
        pytest.skip("frontend/src/api/types.ts is not checked out next to the backend")
    return Contract(TYPES_TS.read_text(encoding="utf-8"))


@pytest.fixture
def faked(monkeypatch):
    install_service_fakes(monkeypatch, [wiggly_route(k) for k in range(3)])


def test_types_ts_is_read(contract):
    assert {"Place", "PlanRequest", "Plan", "Route", "Checkpoint", "Heatmap"} <= set(contract.interfaces)
    assert all(contract.interfaces.values())


def test_plan_from_the_planner_matches_types_ts(contract, faked):
    data = plan()
    assert contract.errors(data, "Plan") == []
    assert data["routes"][0]["checkpoints"], "nothing to check in the checkpoints"


def test_heatmap_from_the_planner_matches_types_ts(contract, faked):
    data = planner.build_heatmap(plan()["plan_id"])
    assert contract.errors(data, "Heatmap") == []
    assert data["points"], "nothing to check in the points"


@override_settings(USE_FIXTURES=False, TOMTOM_API_KEY="test-key")
def test_geocode_results_match_types_ts(contract, monkeypatch):
    found = [tomtom.Place(label="Dallas, TX", lat=32.7767, lon=-96.797)]
    monkeypatch.setattr(tomtom, "geocode", lambda *a, **k: found)
    results = APIClient().get("/api/geocode?q=dallas").json()["results"]
    assert contract.errors(results, "Place[]") == []


@pytest.mark.parametrize(
    "name, type_", [("plan.json", "Plan"), ("heatmap.json", "Heatmap"), ("places.json", "Place[]")]
)
def test_sample_data_matches_types_ts(contract, name, type_):
    assert contract.errors(load_fixture(name), type_) == []


def test_the_checker_catches_drift(contract, faked):
    good = plan()
    cases = {
        "missing": lambda d: d["routes"][0]["checkpoints"][0].pop("eta"),
        "wrong type": lambda d: d["routes"][0].update(rank="1"),
        "unknown level": lambda d: d["routes"][0]["summary"].update(max_level="EXTREME"),
        "level missing from a Record": lambda d: d["routes"][0]["summary"]["miles_by_level"].pop("HIGH"),
        "undeclared field": lambda d: d["input"].update(units="metric"),
        "bad tuple": lambda d: d["routes"][0]["geometry"][0].append(0),
        "bad interval": lambda d: d["input"].update(interval_miles=30),
    }
    for label, break_it in cases.items():
        broken = copy.deepcopy(good)
        break_it(broken)
        assert contract.errors(broken, "Plan"), f"{label} went unnoticed"
