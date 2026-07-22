// Server-side Postgres connection (Supabase Supavisor pooler).
// Only ever import from API routes — never from client components.

import postgres from 'postgres'

const globalForDb = globalThis as unknown as { __btSql?: ReturnType<typeof postgres> }

export function getSql() {
  if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL is not set')
  if (!globalForDb.__btSql) {
    globalForDb.__btSql = postgres(process.env.DATABASE_URL, {
      max: 1,
      idle_timeout: 20,
      connect_timeout: 10,
      prepare: false,
    })
  }
  return globalForDb.__btSql
}
