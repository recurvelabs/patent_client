import json
from pathlib import Path

from .model.biblio import PublicSearchBiblioPage
from .model.document import PublicSearchDocument
from patent_client.util.test import compare_dicts
from patent_client.util.test import compare_lists

fixtures = Path(__file__).parent / "fixtures"


import datetime


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""

    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    raise TypeError("Type %s not serializable" % type(obj))


def test_parse_biblio():
    input_file = fixtures / "biblio.json"
    output_file = fixtures / "biblio_output.json"
    input_data = json.loads(input_file.read_text())
    output_data = PublicSearchBiblioPage.model_validate(input_data)
    # output_file.write_text(output_data.model_dump_json(indent=2))
    expected_data = json.loads(output_file.read_text())
    compare_dicts(json.loads(output_data.model_dump_json()), expected_data)


def test_parse_doc():
    input_file = fixtures / "doc.json"
    output_file = fixtures / "doc_output.json"
    input_data = json.loads(input_file.read_text())
    output_data = [PublicSearchDocument.model_validate(o) for o in input_data]
    # output_file.write_text(json.dumps([o.model_dump() for o in output_data], indent=2, default=json_serial))
    expected_data = json.loads(output_file.read_text())
    output_data = json.loads(json.dumps([o.model_dump() for o in output_data], indent=2, default=json_serial))
    compare_lists(output_data, expected_data)
