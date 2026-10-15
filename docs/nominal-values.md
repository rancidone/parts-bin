# Nominal values

Nominal inventory quantities are stored as exact decimal strings and base units.
For example, a 100 nF capacitor has `value="0.0000001"`, `value_unit="F"`, and a
computed `display_value="100nF"`. A 5.1 kΩ resistor has `value="5100"`,
`value_unit="Ω"`, and `display_value="5k1"`. The domain handles conversion for
all application adapters. Existing notation remains valid input. Current,
voltage, power, frequency and other supported nominal dimensions use the same
mechanism. Include units when the category does not establish the dimension.

Values with unknown units, ranges, or qualifiers remain text. A nominal value
is not a source-backed electrical rating. Original descriptions and evidence
retain their original wording; accepted ratings keep their bases and conditions.

## Upgrade an existing installation

Take a normal PostgreSQL backup and pause intake/workers when coordinating the
upgrade. Rehearse the conversion on a restored copy. Use the additive schema
setup before starting the upgraded application:

```sh
uv run python -m db.postgres.setup --config config.toml
uv run python -m scripts.normalize_nominal_values --config config.toml
uv run python -m scripts.normalize_nominal_values --config config.toml \
  --apply --backup /protected/backups/nominal-values-before.json
```

The preview lists exact field changes, unparsed record IDs and conflicting
identities. Resolve collisions explicitly before applying; a failure commits no
inventory changes. The apply command refuses to overwrite its protected backup.
It preserves quantities, IDs, source evidence and pending reviews. Running the
conversion again is harmless. Afterward, verify quantities, record identities,
unit-aware searches and display labels, and inspect pending reviews normally.

Keep the database backup, inventory backup and prior application image together.
A pre-upgrade application does not understand the canonical decimal/unit pair;
rollback requires compatible code or a coordinated database restore. Do not
replay saved mutations or restore a database over newer inventory writes.
