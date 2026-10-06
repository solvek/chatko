"""The schema, one script per version. A released script is never edited: a change is a new one.

`PRAGMA user_version` holds the number of the last script applied.
"""

MIGRATIONS: tuple[str, ...] = (
    # 1: runtime state. A delivery's recipient is '' when the endpoint has none, so that the unique
    # key works. `seq` keeps the order the deliveries were stored in.
    """
    CREATE TABLE messages (
        id TEXT PRIMARY KEY,
        instance TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        transport_id TEXT NOT NULL,
        received_at TEXT NOT NULL,
        author TEXT NOT NULL,
        text TEXT NOT NULL,
        attachments TEXT NOT NULL,
        from_recipient TEXT,
        UNIQUE (instance, endpoint, transport_id)
    );
    CREATE INDEX messages_received_at ON messages (received_at);

    CREATE TABLE deliveries (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id TEXT NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
        instance TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        recipient TEXT NOT NULL,
        author_label TEXT NOT NULL,
        text TEXT NOT NULL,
        due_at TEXT NOT NULL,
        state TEXT NOT NULL,
        attempts INTEGER NOT NULL,
        truncated INTEGER NOT NULL,
        last_error TEXT,
        UNIQUE (message_id, instance, endpoint, recipient)
    );
    CREATE INDEX deliveries_state ON deliveries (state, seq);

    CREATE TABLE heard (
        kind TEXT NOT NULL,
        external_id TEXT NOT NULL,
        instance TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (kind, external_id, instance, endpoint)
    );

    CREATE TABLE arrivals (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint TEXT NOT NULL,
        at TEXT NOT NULL
    );
    CREATE INDEX arrivals_at ON arrivals (at);

    CREATE TABLE accounts (
        kind TEXT NOT NULL,
        external_id TEXT NOT NULL,
        display_name TEXT NOT NULL,
        short_name TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        PRIMARY KEY (kind, external_id)
    );
    """,
    # 2: when an account was first seen at a site of a group; the notice for a new account is only
    # for those (D66). The accounts known before were told about under the old rule, or the admin
    # did not ask: they are not told again.
    """
    ALTER TABLE accounts ADD COLUMN first_at_site TEXT;
    UPDATE accounts SET first_at_site = first_seen;
    """,
)
