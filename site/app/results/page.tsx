import { redirect } from 'next/navigation'
import { addDays } from '../lib/pmResults'

/** /results → yesterday. On the server that is yesterday in UTC; the day strip
 *  links straight to the reader's own yesterday once the page has hydrated. */
export const dynamic = 'force-dynamic'

export default function ResultsIndex() {
  redirect(`/results/${addDays(new Date().toISOString().slice(0, 10), -1)}`)
}
