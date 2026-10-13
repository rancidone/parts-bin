import json
import sys

import pytest

from tests.storage import TestRepository
from domain import AddPartRequest, PartFields, PartsBinService
from ingestion import review_specifications
from ingestion.review_specifications import main


def test_operator_import_stages_assertion_without_mutating_quantity(tmp_path, monkeypatch, capsys):
    database = tmp_path / 'parts.db'
    service = PartsBinService(TestRepository(database))
    part = service.add_part(AddPartRequest(PartFields('resistor', 'passive', 4, value='10k')))
    candidate = tmp_path / 'candidate.json'
    candidate.write_text(json.dumps([{'name': 'tolerance', 'value': '1 %', 'basis': 'maximum',
        'conditions': {}, 'evidence': {'kind': 'user_assertion', 'excerpt': 'The label says 1%.'}}]))
    config = tmp_path / 'config.toml'
    config.write_text('[db]\nhost = "isolated"\n')
    monkeypatch.setattr(review_specifications, 'from_config', lambda config: service.repository)
    monkeypatch.setattr(sys, 'argv', ['review_specifications', str(part.id), str(candidate), '--config', str(config)])
    main()
    output = json.loads(capsys.readouterr().out)
    assert output['facts'] == []
    assert output['pending_review']['facts'][0]['evidence']['kind'] == 'user_assertion'
    assert service.list() == [part]


def test_operator_import_does_not_create_missing_inventory(tmp_path, monkeypatch):
    database = tmp_path / 'missing.db'
    monkeypatch.setattr(sys, 'argv', ['review_specifications', '1', str(tmp_path / 'candidate'), '--config', str(database)])
    with pytest.raises(SystemExit):
        main()
    assert not database.exists()
