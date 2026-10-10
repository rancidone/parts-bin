# Configuration decisions

## Explicit choices

Configure one OpenAI Responses API agent and only the source integrations needed
for enrichment. A missing API key or unavailable provider must fail visibly;
there is no alternate runtime to select automatically.

Keep configuration outside application images and provider credentials outside
version control, prompts, logs, and test fixtures. Each installation should use its own API credentials, not a developer's personal
interactive login. The cloud secret-delivery mechanism is a separate deployment
decision.

## One reference for keys

Use [config.example.toml](../../config.example.toml) as the configuration example
and follow the loading/validation code from [local_app.py](../../local_app.py) for exact
behavior. Do not maintain another TOML schema or default-model list in prose.
For container mounts and environment variables, inspect
[compose.yaml](../../compose.yaml) and [Dockerfile](../../Dockerfile).

Local configuration is loaded when the local ASGI factory is invoked, not when
HTTP routes are imported. The HTTP app receives assembled services. Cloud and
worker entry points must provide their own explicit configuration and storage
composition through the shared [application assembly](../../application.py);
they must not assume a local TOML file or silently select another model backend.

Configured presence is not provider authentication or functional readiness. Use
an explicit end-to-end request when checking a runtime; account for external
request costs and avoid exposing credentials while diagnosing failures.
