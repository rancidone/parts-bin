"""Registration keeps signing material out of output and preserves config ownership."""

from types import SimpleNamespace
import tomllib

import pytest

from scripts import openai_webhook


def test_register_saves_only_protected_config_and_refuses_repeat(tmp_path, monkeypatch, capsys):
    config = tmp_path / 'config.toml'
    config.write_text('[agent.openai]\napi_key = "test-key"\nmodel = "test-model"\n\n[db]\nhost = "preserved"\n')
    config.chmod(0o600)
    endpoint = SimpleNamespace(id='endpoint-test', signing_secret='private-signing-material')
    calls = []
    class Client:
        def __init__(self, **settings):
            assert settings['max_retries'] == 0
            self.webhooks = self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def list(self): return []
        def create(self, **parameters):
            calls.append(parameters)
            return endpoint
    monkeypatch.setattr(openai_webhook, 'OpenAI', Client)
    openai_webhook.register(config, 'https://parts.example/agent/webhooks/openai')
    result = tomllib.loads(config.read_text())
    assert result['db']['host'] == 'preserved'
    assert result['agent']['openai']['model'] == 'test-model'
    assert result['agent']['openai']['webhook_secret'] == endpoint.signing_secret
    assert config.stat().st_mode & 0o777 == 0o600
    assert len(calls) == 1
    assert set(calls[0]['event_types']) == openai_webhook.TERMINAL_EVENTS
    assert endpoint.signing_secret not in capsys.readouterr().out
    with pytest.raises(ValueError, match='already configured'):
        openai_webhook.register(config, 'https://parts.example/agent/webhooks/openai')
