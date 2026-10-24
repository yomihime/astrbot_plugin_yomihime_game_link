-- Business event identity is independent of templates/composers/format versions.
-- Adopt existing immutable delivery history before creating any new windows.
CREATE TABLE IF NOT EXISTS b04_public_event_identity (
    business_key TEXT PRIMARY KEY,
    window_id TEXT NOT NULL REFERENCES b04_digest_windows(window_id)
);
INSERT OR IGNORE INTO b04_public_event_identity(business_key,window_id)
SELECT json_array(
    json_extract(s.record_json,'$.fields.module_id'),
    json(json_extract(dm.association_json,'$.fields.event.fields.recipient')),
    dm.event_key
), dm.window_id
FROM b04_digest_members dm
JOIN b04_subscriptions s ON s.subscription_id=dm.subscription_id
LEFT JOIN b04_digest_envelopes de ON de.window_id=dm.window_id AND de.recipient_key=dm.recipient_key
WHERE dm.window_id LIKE 'public-event:v1:%'
ORDER BY CASE de.state WHEN 'sent' THEN 0 WHEN 'unknown' THEN 1 WHEN 'sending' THEN 2 ELSE 3 END,
dm.window_id, dm.subscription_id;
