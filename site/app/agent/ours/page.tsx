/** The public record lived here until 2026-09-11. The pressure arms are the
 *  operator's own agents now, shown to him on /agent and nowhere else. The
 *  redirect in next.config.mjs normally catches this path first; this page is
 *  the fallback so the old URL can never render a stale public record. */

import { redirect } from 'next/navigation'

export default function OurAgentMoved() {
  redirect('/agent')
}
