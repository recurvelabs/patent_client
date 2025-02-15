import json
from pathlib import Path

import lxml.etree as ET

from .schema import FamilySchema
from patent_client.util.test import compare_dicts

fixtures = Path(__file__).parent / "fixtures"


def test_example():
    tree = ET.parse(fixtures / "family_input.xml")
    result = FamilySchema().load(tree)
    expected_file = fixtures / "family_convert.json"
    # expected_file.write_text(result.to_json(indent=2))
    expected = json.loads(expected_file.read_text())
    compare_dicts(json.loads(result.to_json()), expected)
