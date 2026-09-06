/** Sub-tabs inside the Agent dashboard. The top-level tabs are real routes and
 *  live in components/AppShell.tsx — these are the two views the dashboard has
 *  always had, minus the newsletter and about sections the SaaS shell replaced. */
export type Section = 'overview' | 'strategies'

export const VALID_SECTIONS: Section[] = ['overview', 'strategies']
