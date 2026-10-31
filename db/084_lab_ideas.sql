-- 084: every idea written on Create, and whether it became a strategy.
--
-- "What do people ask for?" is the question that picks what the strategy language
-- learns next, and on 2026-10-30 it had no answer: a refused idea left no trace
-- anywhere. Only an idea saved as an agent was kept, and nobody saves a refusal.
--
-- 🔑 The words and the outcome, nothing about who wrote them: this table is for
--    counting ideas, not people. The privacy page already lists the ideas you
--    describe under what we keep.
--
-- Server only: RLS on with no policies, like usage_events.

CREATE TABLE IF NOT EXISTS public.lab_ideas (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    idea       TEXT NOT NULL,          -- the words, with any answers appended
    kind       TEXT NOT NULL CHECK (kind IN ('prematch', 'inplay')),
    supported  BOOLEAN NOT NULL,
    reason     TEXT,                   -- why not, as the user was told
    suggestion TEXT,                   -- the nearest thing offered instead
    spec       JSONB                   -- what it became
);

COMMENT ON TABLE public.lab_ideas IS
  'Every idea written on Create and what became of it. No author. Read it to choose what the strategy language learns next.';

ALTER TABLE public.lab_ideas ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.lab_ideas FROM PUBLIC;
DO $$ BEGIN
    REVOKE ALL ON TABLE public.lab_ideas FROM anon, authenticated;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;

-- What the next batch reads:
--   select supported, kind, count(*) from lab_ideas group by 1, 2;
--   select idea, reason from lab_ideas where not supported order by id desc limit 100;
