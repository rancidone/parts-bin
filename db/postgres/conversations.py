"""Postgres conversation identity, ordered events and publication deduplication."""

import json

from agent_runtime.models import ConversationEvent, ConversationSummary
from agent_runtime.store import RuntimeSelectionError, UnsupportedRuntimeError, conversation_title
from .connection import Adapter, access
from .records import encode


class PostgresConversationRepository(Adapter):
    @access(write=True)
    def create_thread(self, thread_id, runtime):
        row = self.execute('SELECT runtime FROM agent_threads WHERE thread_id = %s', (thread_id,)).fetchone()
        if row is None:
            if runtime != 'openai':
                raise UnsupportedRuntimeError('Only OpenAI conversations can be created.')
            order = self.execute('SELECT next_thread_order FROM pb_revision WHERE id = 1').fetchone()['next_thread_order']
            self.execute('UPDATE pb_revision SET next_thread_order = next_thread_order + 1 WHERE id = 1')
            self.execute('INSERT INTO agent_threads VALUES (%s, %s, %s)', (thread_id, runtime, order))
        elif row['runtime'] != runtime:
            raise RuntimeSelectionError(f"Thread {thread_id!r} is bound to {row['runtime']!r}, not {runtime!r}")

    @access()
    def runtime_for(self, thread_id):
        row = self.execute('SELECT runtime FROM agent_threads WHERE thread_id = %s', (thread_id,)).fetchone()
        return None if row is None else row['runtime']

    @access()
    def threads(self):
        rows = self.execute('''SELECT t.thread_id, t.runtime, t.thread_order, COALESCE(MAX(e.sequence), 0) AS last_sequence
            FROM agent_threads t LEFT JOIN agent_events e ON e.thread_id = t.thread_id
            GROUP BY t.thread_id, t.runtime, t.thread_order ORDER BY t.thread_order DESC''').fetchall()
        summaries = []
        for row in rows:
            events = self.execute('''SELECT kind, data_json FROM agent_events WHERE thread_id = %s
                AND kind IN ('user_message', 'assistant_text') ORDER BY sequence''', (row['thread_id'],)).fetchall()
            summaries.append(ConversationSummary(row['thread_id'], row['runtime'], conversation_title(events), row['last_sequence']))
        return summaries

    def append(self, event):
        return self._append(event, None)

    def append_once(self, event, event_id):
        return self._append(event, event_id)

    @access(write=True)
    def _append(self, event, event_id):
        self.create_thread(event.thread_id, event.runtime)
        if event_id is not None:
            previous = self.execute('''SELECT e.* FROM agent_events e JOIN agent_event_keys k
                ON e.thread_id = k.thread_id AND e.sequence = k.sequence WHERE k.event_id = %s''', (event_id,)).fetchone()
            if previous is not None:
                if previous['thread_id'] != event.thread_id:
                    raise ValueError('Event identity belongs to another conversation')
                return self._event(previous, event.thread_id)
        sequence = self.execute('SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM agent_events WHERE thread_id = %s',
                                (event.thread_id,)).fetchone()['next_sequence']
        self.execute('INSERT INTO agent_events VALUES (%s, %s, %s, %s, %s)',
                     (event.thread_id, sequence, event.kind, event.runtime, encode(event.data)))
        if event_id is not None:
            self.execute('INSERT INTO agent_event_keys VALUES (%s, %s, %s)', (event_id, event.thread_id, sequence))
        return ConversationEvent(event.kind, event.thread_id, event.runtime, event.data, sequence)

    @staticmethod
    def _event(row, thread_id):
        return ConversationEvent(row['kind'], thread_id, row['runtime'], json.loads(row['data_json']), row['sequence'])

    @access()
    def events(self, thread_id):
        return [self._event(row, thread_id) for row in self.execute(
            'SELECT * FROM agent_events WHERE thread_id = %s ORDER BY sequence', (thread_id,)).fetchall()]
