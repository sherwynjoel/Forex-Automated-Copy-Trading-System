-- Admin-set, one-time account cutoff date. Two days before cutoff_date the
-- copier's reminder scanner logs a 'reminder' event (in-app feed + Telegram
-- via the API's notifier). cutoff_reminder_sent_for records WHICH cutoff
-- value the reminder covered, so each distinct date reminds exactly once --
-- an admin moving the date re-arms the reminder, a restart does not repeat
-- it. Both nullable: no cutoff means no reminder.
ALTER TABLE accounts ADD COLUMN cutoff_date DATE;
ALTER TABLE accounts ADD COLUMN cutoff_reminder_sent_for DATE;

-- 'reminder' event category: date-based nudges to the operator, distinct
-- from risk (broker facts) and control (operator actions).
ALTER TABLE events DROP CONSTRAINT events_category_check;
ALTER TABLE events ADD CONSTRAINT events_category_check
    CHECK (category IN ('master_event', 'slave_action', 'connection', 'auth',
                        'drift', 'control', 'risk', 'reminder'));
