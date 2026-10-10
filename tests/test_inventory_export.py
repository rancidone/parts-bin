import csv
import io

from inventory_export import export_csv


def test_column_order():
    header = export_csv([]).splitlines()[0]
    assert header == "part_category,value,package,quantity,part_number,manufacturer,description,datasheet_url"


def test_row_content():
    rows = [{"part_category": "resistor", "value": "10k", "quantity": 4,
             "description": 'Measured, marked "precision"'}]
    exported = list(csv.DictReader(io.StringIO(export_csv(rows))))
    assert exported[0]["part_category"] == "resistor"
    assert exported[0]["value"] == "10k"
    assert exported[0]["quantity"] == "4"
    assert exported[0]["description"] == rows[0]["description"]
