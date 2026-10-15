"""
Checks on the Power BI project without opening Power BI:
- every report file validates against Microsoft's published PBIR JSON schemas
- every field a visual or filter uses exists in the semantic model
- every DAX reference (table, column, measure) resolves, with no circular measures
- TMDL uses tab indentation only (TMDL rejects mixed indentation)
"""
import json
import re
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "powerbi"))
import build_model as bm  # noqa: E402

REPORT = ROOT / "powerbi" / f"{bm.NAME}.Report"
SCHEMA_PREFIX = "https://developer.microsoft.com/json-schemas/"
SCHEMA_RAW = "https://raw.githubusercontent.com/microsoft/json-schemas/main/"
SCHEMA_CACHE = ROOT / ".cache" / "json-schemas"


def model_fields():
    cols = {name: set(c) for name, _, _, c in bm.CSV_TABLES}
    cols["P&L Line"] = {"Line", "Sort", "Format", "Favourable", "Style"}
    cols["Revenue Growth Flex"] = {"Revenue Growth Flex"}
    cols["Opex Flex"] = {"Opex Flex"}
    cols[bm.MEASURES_TABLE] = {"Placeholder"}
    return cols, {m[0] for m in bm.MEASURES}


def report_files():
    files = [p for p in REPORT.rglob("*.json") if "StaticResources" not in p.parts]
    return files + [REPORT / "definition.pbir", ROOT / "powerbi" / f"{bm.NAME}.pbip"]


def _schema(uri):
    rel = uri.removeprefix(SCHEMA_PREFIX)
    path = SCHEMA_CACHE / rel
    if not path.exists():
        try:
            res = requests.get(SCHEMA_RAW + rel, timeout=20)
            res.raise_for_status()
        except requests.RequestException as exc:
            pytest.skip(f"can't download PBIR schemas ({exc})")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(res.content)
    return json.loads(path.read_text())


@pytest.mark.parametrize("path", report_files(), ids=lambda p: str(p.relative_to(ROOT / "powerbi")))
def test_report_files_match_microsoft_schemas(path):
    from jsonschema import Draft7Validator, validators
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT7

    doc = json.loads(path.read_text())
    registry = Registry(retrieve=lambda uri: Resource.from_contents(_schema(uri), default_specification=DRAFT7))
    schema = _schema(doc["$schema"])
    validator = validators.validator_for(schema, default=Draft7Validator)(schema, registry=registry)
    errors = [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in validator.iter_errors(doc)]
    assert not errors, errors[:5]


def _field_refs(node):
    """(kind, entity, property) for every Column/Measure reference with a named Entity."""
    if isinstance(node, dict):
        for kind in ("Column", "Measure"):
            if kind in node and isinstance(node[kind], dict):
                src = node[kind].get("Expression", {}).get("SourceRef", {})
                if "Entity" in src:
                    yield kind, src["Entity"], node[kind]["Property"]
        for v in node.values():
            yield from _field_refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _field_refs(v)


def test_every_report_field_exists_in_the_model():
    cols, measures = model_fields()
    missing = []
    for path in REPORT.rglob("visual.json"):
        for kind, entity, prop in _field_refs(json.loads(path.read_text())):
            ok = prop in measures if kind == "Measure" else prop in cols.get(entity, ())
            if kind == "Measure" and entity != bm.MEASURES_TABLE:
                ok = False
            if not ok:
                missing.append(f"{path.parent.name}: {kind} {entity}[{prop}]")
    assert not missing, missing


def test_query_refs_match_fields():
    bad = []
    for path in REPORT.rglob("visual.json"):
        doc = json.loads(path.read_text())
        for role in doc["visual"].get("query", {}).get("queryState", {}).values():
            for p in role["projections"]:
                (kind, ref), = [(k, v) for k, v in p["field"].items()]
                expected = f'{ref["Expression"]["SourceRef"]["Entity"]}.{ref["Property"]}'
                if p["queryRef"] != expected:
                    bad.append((path.parent.name, p["queryRef"], expected))
    assert not bad, bad


def test_visual_names_are_unique_and_valid():
    names = [json.loads(p.read_text())["name"] for p in REPORT.rglob("visual.json")]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"[\w-]+", n) for n in names)


def test_dax_references_resolve():
    cols, measures = model_fields()
    problems = []
    texts = [(m[0], m[1]) for m in bm.MEASURES] + [(f"format:{k}", v) for k, v in bm.DYNAMIC_FORMATS.items()]
    for name, dax in texts:
        code = re.sub(r'"[^"]*"', '""', dax)
        for a, b, col in re.findall(r"(?:'([^']+)'|\b([A-Za-z_][A-Za-z_]*))\[([^\]]+)\]", code):
            table = a or b
            if table not in cols:
                problems.append(f"{name}: table {table}")
            elif col not in cols[table]:
                problems.append(f"{name}: column {table}[{col}]")
        for ref in re.findall(r"(?<![\w'\]])\[([^\]]+)\]", code):
            if ref not in measures:
                problems.append(f"{name}: measure [{ref}]")
    assert not problems, problems


def test_no_measures_inside_calculate_boolean_filters():
    """DAX forbids measures in CALCULATE's boolean filter arguments; they must be captured in a VAR."""
    offenders = [m[0] for m in bm.MEASURES if re.search(r"\]\s*=\s*\[(?!_)[^\]]+\]\s*[,)]", m[1])]
    assert not offenders, offenders


def test_no_circular_measures():
    measures = {m[0]: m[1] for m in bm.MEASURES}
    deps = {n: set(re.findall(r"(?<![\w'\]])\[([^\]]+)\]", re.sub(r'"[^"]*"', '""', d))) & set(measures) for n, d in measures.items()}
    visiting, done = set(), set()

    def visit(n):
        assert n not in visiting, f"circular reference through {n}"
        if n in done:
            return
        visiting.add(n)
        for d in deps[n]:
            visit(d)
        visiting.discard(n)
        done.add(n)

    for n in deps:
        visit(n)


def test_tmdl_uses_tab_indentation():
    bad = []
    for path in (ROOT / "powerbi" / f"{bm.NAME}.SemanticModel" / "definition").rglob("*.tmdl"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line and line[0] == " ":
                bad.append(f"{path.name}:{i}")
    assert not bad, bad[:10]


def test_model_csv_columns_match_data():
    bm.check_csv_headers()


def test_table_names_are_allowed_by_power_bi():
    """Power BI rejects tables named like engine objects ("Measures" is the MDX name for all measures)."""
    import build_report as br

    reserved = {"measures", "$system", "model", "database"}
    tables = [t[0] for t in bm.CSV_TABLES] + ["P&L Line", "Revenue Growth Flex", "Opex Flex", bm.MEASURES_TABLE]
    assert not [t for t in tables if t.lower() in reserved]
    assert br.MEASURES_TABLE == bm.MEASURES_TABLE


def test_conditional_formatting_selectors_point_at_real_measures():
    _, measures = model_fields()
    bad = []
    for path in REPORT.rglob("visual.json"):
        for m in re.findall(r'"metadata": "([^"]+)"', path.read_text()):
            table, _, name = m.partition(".")
            if table != bm.MEASURES_TABLE or name not in measures:
                bad.append((path.parent.name, m))
    assert not bad, bad
