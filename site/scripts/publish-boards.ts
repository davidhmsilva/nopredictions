/** The board publisher: every sweep the site used to run, once a minute, on
 *  the agent server, written as one JSON file per board.
 *
 *  Vercel paused the site on 2026-10-07 for Fluid Active CPU. The sweeps were
 *  the bill; here they run once for everybody, and the site reads the files
 *  (`app/lib/remoteBoard.ts`, `BOARDS_URL`). Same code as the site's: the
 *  modules are imported, not copied, so the two cannot drift.
 *
 *    NP_PUBLISHER=1 BOARDS_DIR=/srv/boards npx tsx scripts/publish-boards.ts
 *    ... --once        one cycle, then exit
 *
 *  Each board is built and written on its own: one failing costs that board
 *  for a cycle, and the file it wrote last stays up.
 */

import { mkdir, rename, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

if (process.env.NP_PUBLISHER !== '1') {
  console.error('set NP_PUBLISHER=1: the board modules need it to run outside Next')
  process.exit(2)
}

const OUT = process.env.BOARDS_DIR || '/srv/boards'
const CYCLE_MS = Number(process.env.BOARDS_CYCLE_MS || 60_000)
const ONCE = process.argv.includes('--once')

async function write(name: string, body: unknown): Promise<number> {
  const json = JSON.stringify(body)
  const tmp = join(OUT, `.${name}.json.tmp`)
  await writeFile(tmp, json)
  // Atomic: a reader never sees a half-written file.
  await rename(tmp, join(OUT, `${name}.json`))
  return json.length
}

async function main() {
  // Imported after the guard so the modules read NP_PUBLISHER at load.
  const { sweep } = await import('../app/lib/scoutCache')
  const { buildSportBoard } = await import('../app/lib/sports')
  const { pricedIndex } = await import('../app/lib/kalshiSoccer')
  const { SPORT_KEYS } = await import('../app/lib/sportsMeta')

  await mkdir(OUT, { recursive: true })

  const jobs: [string, () => Promise<unknown>][] = [
    ['scout', () => sweep()],
    ['kalshi-soccer', () => pricedIndex()],
    ...SPORT_KEYS.map((k) => [`sport-${k}`, () => buildSportBoard(k)] as [string, () => Promise<unknown>]),
  ]

  for (;;) {
    const started = Date.now()
    const results = await Promise.allSettled(
      jobs.map(async ([name, build]) => {
        const t0 = Date.now()
        const bytes = await write(name, await build())
        return `${name} ${(bytes / 1024).toFixed(0)}kB ${((Date.now() - t0) / 1000).toFixed(1)}s`
      })
    )
    const line = results
      .map((r, i) => (r.status === 'fulfilled' ? r.value : `${jobs[i][0]} FAILED: ${String(r.reason).slice(0, 160)}`))
      .join(' | ')
    console.log(`${new Date().toISOString()} ${line}`)
    if (ONCE) return
    await new Promise((r) => setTimeout(r, Math.max(5_000, CYCLE_MS - (Date.now() - started))))
  }
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
