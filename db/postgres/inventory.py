"""Relational inventory, reviewed evidence and candidate queries for Postgres."""

from datetime import datetime, timezone
import json

from domain.models import CategorySummary, Part, EDITABLE_PART_FIELDS
from domain.repositories import RepositoryConflict
from .connection import Adapter, access
from .records import encode

_METADATA = ('part_category', 'profile', 'value', 'package', 'part_number', 'manufacturer')
_FIELDS = tuple(sorted(EDITABLE_PART_FIELDS))


def now():
    return datetime.now(timezone.utc).isoformat()


def passive_identity(fields):
    # Match the SQLite index: null values/packages do not collide at storage level.
    # The domain's normalized identity checks additionally protect unknown package.
    if (fields.get('part_number') is None and fields['part_category'].lower() in {'resistor', 'capacitor', 'inductor'}
            and fields.get('value') is not None and fields.get('package') is not None):
        return encode([fields['part_category'], fields['value'], fields['package']])
    return None


class PostgresInventoryRepository(Adapter):
    @access()
    def search(self, filters):
        conditions, values = [], []
        for name in ('part_category', 'profile', 'value', 'package', 'part_number'):
            if filters.get(name) is not None:
                conditions.append(f'{name} = %s')
                values.append(filters[name])
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        rows = self.execute('SELECT * FROM parts' + where +
            ' ORDER BY part_category, value NULLS FIRST, part_number NULLS FIRST, id', values).fetchall()
        return [Part.from_row(row) for row in rows]

    @access()
    def list_categories(self):
        return [CategorySummary(**row) for row in self.execute('''SELECT part_category,
            COUNT(*) AS part_count, CAST(SUM(quantity) AS BIGINT) AS total_quantity
            FROM parts GROUP BY part_category ORDER BY part_category''').fetchall()]

    @access()
    def get(self, part_id):
        row = self.execute('SELECT * FROM parts WHERE id = %s', (part_id,)).fetchone()
        return None if row is None else Part.from_row(row)

    @access(write=True)
    def insert(self, fields):
        part_id = self.execute('SELECT next_part_id FROM pb_revision WHERE id = 1').fetchone()['next_part_id']
        self.execute('UPDATE pb_revision SET next_part_id = next_part_id + 1 WHERE id = 1')
        stamp = now()
        names = ('id', *_FIELDS, 'passive_identity', 'created_at', 'updated_at')
        values = (part_id, *(fields.get(name) for name in _FIELDS), passive_identity(fields), stamp, stamp)
        self.execute(f"INSERT INTO parts ({', '.join(names)}) VALUES ({', '.join('%s' for _ in names)})", values)
        return part_id

    @access(write=True)
    def increment_stock(self, part_id, quantity):
        cursor = self.execute('UPDATE parts SET quantity = quantity + %s, updated_at = %s WHERE id = %s',
                             (quantity, now(), part_id))
        if cursor.rowcount != 1:
            raise KeyError(part_id)

    @access(write=True)
    def replace_parts(self, updates):
        for part_id, fields in updates:
            self._replace(part_id, fields)

    def _replace(self, part_id, fields):
        part = self.get(part_id)
        if part is None:
            raise KeyError(part_id)
        changes = {key: value for key, value in fields.items() if key in _FIELDS}
        changes['passive_identity'] = passive_identity({**vars(part), **changes})
        changes['updated_at'] = now()
        self.execute('UPDATE parts SET ' + ', '.join(f'{key} = %s' for key in changes) + ' WHERE id = %s',
                     (*changes.values(), part_id))

    @access(write=True)
    def delete(self, part_id):
        # Explicit deletion avoids depending on distributed cascading-FK support.
        for table in ('part_pending_field_review', 'part_field_provenance',
                      'part_specifications', 'part_specification_reviews'):
            self.execute(f'DELETE FROM {table} WHERE part_id = %s', (part_id,))
        self.execute('DELETE FROM parts WHERE id = %s', (part_id,))

    @access()
    def list_pending_reviews(self):
        reviews = {}
        for row in self.execute('SELECT * FROM part_pending_field_review ORDER BY part_id, field_name').fetchall():
            review = reviews.setdefault(row['part_id'], {'fields': {}, 'provenance': [], 'outcome': 'saved'})
            review['fields'][row['field_name']] = {'accepted': True, 'value': row['proposed_value'] or ''}
            review['provenance'].append(json.loads(row['provenance_json']))
        return reviews

    @access(write=True)
    def save_pending_review(self, part_id, fields, provenance):
        if self.get(part_id) is None:
            raise RepositoryConflict('Review target no longer exists')
        self.clear_pending_review(part_id)
        by_field = {record['field_name']: record for record in provenance}
        stamp = now()
        for name, value in fields.items():
            evidence = by_field.get(name, {'field_name': name, 'field_value': value})
            self.execute('''INSERT INTO part_pending_field_review
                (part_id, field_name, proposed_value, provenance_json, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)''',
                (part_id, name, None if value is None else str(value), encode(evidence), stamp, stamp))

    @access(write=True)
    def stage_enrichment_if_unchanged(self, original, fields, provenance):
        current = self.get(original.id)
        metadata = (*_METADATA, 'description')
        if (current is None or any(getattr(current, name) != getattr(original, name) for name in metadata)
                or original.id in self.list_pending_reviews()):
            return False
        self.save_pending_review(original.id, fields, provenance)
        return True

    @access(write=True)
    def clear_pending_review(self, part_id, fields=None):
        if fields:
            self.execute('DELETE FROM part_pending_field_review WHERE part_id = %s AND field_name IN (' +
                         ', '.join('%s' for _ in fields) + ')', (part_id, *fields))
        else:
            self.execute('DELETE FROM part_pending_field_review WHERE part_id = %s', (part_id,))

    @access(write=True)
    def update_with_provenance(self, part_id, fields, provenance):
        self._replace(part_id, {name: value for name, value in fields.items() if value is not None and name != 'quantity'})
        stamp = now()
        for record in provenance:
            names = ('field_name', 'field_value', 'source_tier', 'source_kind', 'source_locator',
                     'extraction_method', 'confidence_marker', 'conflict_status', 'normalization_method',
                     'evidence', 'competing_candidates')
            values = {name: record.get(name) for name in names}
            for name in ('field_name', 'source_tier', 'source_kind', 'extraction_method'):
                values[name] = record[name]
            values['conflict_status'] = record.get('conflict_status', 'clear')
            values['competing_candidates'] = encode(record.get('competing_candidates', []))
            columns = ('part_id', *names, 'created_at', 'updated_at')
            self.execute(f"INSERT INTO part_field_provenance ({', '.join(columns)}) VALUES " +
                f"({', '.join('%s' for _ in columns)}) ON CONFLICT(part_id, field_name) DO UPDATE SET " +
                ', '.join(f'{name} = excluded.{name}' for name in (*names[1:], 'updated_at')),
                (part_id, *values.values(), stamp, stamp))

    @access()
    def list_provenance(self, part_id):
        rows = self.execute('SELECT * FROM part_field_provenance WHERE part_id = %s ORDER BY field_name', (part_id,)).fetchall()
        return [{key: value for key, value in row.items() if key not in {'part_id', 'created_at', 'updated_at'}} for row in rows]

    @access()
    def specifications(self, part_id):
        row = self.execute('SELECT facts_json FROM part_specifications WHERE part_id = %s', (part_id,)).fetchone()
        return [] if row is None else json.loads(row['facts_json'])

    @access()
    def specifications_for_parts(self, part_ids):
        identifiers = list(dict.fromkeys(part_ids))
        found = {}
        for offset in range(0, len(identifiers), 500):
            chunk = identifiers[offset:offset + 500]
            rows = self.execute('SELECT part_id, facts_json FROM part_specifications WHERE part_id IN (' +
                                ', '.join('%s' for _ in chunk) + ')', chunk).fetchall()
            found.update({row['part_id']: json.loads(row['facts_json']) for row in rows})
        return found

    @access()
    def specification_reviews(self):
        return {row['part_id']: {'facts': json.loads(row['facts_json']), 'snapshot': json.loads(row['snapshot_json'])}
                for row in self.execute('SELECT * FROM part_specification_reviews ORDER BY part_id').fetchall()}

    def _spec_snapshot(self, part_id):
        part = self.get(part_id)
        return None if part is None else {'metadata': {name: getattr(part, name) for name in _METADATA},
                                         'facts': self.specifications(part_id)}

    @access(write=True)
    def stage_specifications(self, original, facts, existing):
        snapshot = {'metadata': {name: getattr(original, name) for name in _METADATA}, 'facts': existing}
        if snapshot != self._spec_snapshot(original.id) or original.id in self.specification_reviews():
            return False
        self.execute('INSERT INTO part_specification_reviews VALUES (%s, %s, %s)',
                     (original.id, encode(facts), encode(snapshot)))
        return True

    @access(write=True)
    def apply_specification_review(self, part_id):
        review = self.specification_reviews().get(part_id)
        if review is None or review['snapshot'] != self._spec_snapshot(part_id):
            raise RepositoryConflict('Specification review or target changed')
        current = {fact['name']: fact for fact in self.specifications(part_id)}
        current.update({fact['name']: fact for fact in review['facts']})
        self.execute('''INSERT INTO part_specifications VALUES (%s, %s)
            ON CONFLICT(part_id) DO UPDATE SET facts_json = excluded.facts_json''', (part_id, encode(list(current.values()))))
        self.execute('UPDATE parts SET updated_at = %s WHERE id = %s', (now(), part_id))
        self.reject_specification_review(part_id)

    @access(write=True)
    def reject_specification_review(self, part_id):
        self.execute('DELETE FROM part_specification_reviews WHERE part_id = %s', (part_id,))
