/** The Marketplace's public reads, shared between instances and kept through a
 *  stalled database.
 *
 *  Published agents are the same for everybody and move when a game settles, so
 *  they sit in Next's Data Cache. Past FRESH_S the cached answer is still served
 *  at once while a fresh read runs in the background, and if that read fails the
 *  old answer stays. A failed read is never cached. So once the list has loaded,
 *  a stall costs freshness, not the page: on 2026-10-30 a one-row read took
 *  minutes for over seven hours, and the Marketplace told every visitor it "did
 *  not load" with nine agents published.
 *
 *  What a viewer owns or copied is not here: that is per viewer, and it comes from
 *  /api/marketplace/me.
 *
 *  Server only.
 */

import { unstable_cache } from 'next/cache'
import { getPublished, listPublished } from './agents'

/** A record moves when a game settles, not by the second. */
const FRESH_S = 120

/** Publishing, unpublishing, renaming or archiving drops this at once
 *  (/api/agents/[id]): "take it off the Marketplace" must not take two minutes. */
export const MARKETPLACE_TAG = 'marketplace'

export const publishedAgents = unstable_cache(() => listPublished(), ['marketplace-list-v1'], {
  revalidate: FRESH_S,
  tags: [MARKETPLACE_TAG],
})

/** Null, cached like anything else, when the agent is private, archived or missing. */
export const publishedAgent = unstable_cache((id: number) => getPublished(id), ['marketplace-agent-v1'], {
  revalidate: FRESH_S,
  tags: [MARKETPLACE_TAG],
})
