CREATE TABLE IF NOT EXISTS principals (
    principal_id TEXT PRIMARY KEY,
    identity_namespace TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    UNIQUE (identity_namespace, external_user_id)
);

CREATE TABLE IF NOT EXISTS identities (
    identity_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    FOREIGN KEY (principal_id) REFERENCES principals(principal_id) ON DELETE CASCADE,
    UNIQUE (provider, subject)
);

CREATE TABLE IF NOT EXISTS conversations (
    adapter_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('direct', 'group')),
    delivery_route TEXT NOT NULL,
    PRIMARY KEY (adapter_id, conversation_id)
);

CREATE TABLE IF NOT EXISTS bindings (
    binding_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    principal_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    origin TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    FOREIGN KEY (principal_id) REFERENCES principals(principal_id) ON DELETE CASCADE,
    FOREIGN KEY (adapter_id, conversation_id)
        REFERENCES conversations(adapter_id, conversation_id) ON DELETE CASCADE,
    UNIQUE (binding_id, principal_id, module_id, adapter_id, conversation_id),
    UNIQUE (principal_id, module_id, adapter_id, conversation_id, object_type, object_id)
);

CREATE TABLE IF NOT EXISTS binding_defaults (
    principal_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    PRIMARY KEY (principal_id, module_id, adapter_id, conversation_id),
    FOREIGN KEY (principal_id) REFERENCES principals(principal_id) ON DELETE CASCADE,
    FOREIGN KEY (adapter_id, conversation_id)
        REFERENCES conversations(adapter_id, conversation_id) ON DELETE CASCADE,
    FOREIGN KEY (binding_id, principal_id, module_id, adapter_id, conversation_id)
        REFERENCES bindings(binding_id, principal_id, module_id, adapter_id, conversation_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_identities_principal ON identities(principal_id);
CREATE INDEX IF NOT EXISTS idx_conversations_id ON conversations(conversation_id);
CREATE INDEX IF NOT EXISTS idx_bindings_scope
    ON bindings(principal_id, module_id, adapter_id, conversation_id);
