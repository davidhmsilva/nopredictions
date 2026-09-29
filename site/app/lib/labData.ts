// What the Lab replays theories over, as the site quotes it.
//
// No imports on purpose: the home, login and pricing pages quote these, and
// must not pull the Lab's parser (zod) into their bundles to print a number.
// It was five hand-typed copies of "111,475" before, which is how it stayed
// at the January count while the dataset moved.
//
// Update after `SELECT refresh_bt_lab()` (db/062) changes the count:
//   SELECT count(*), max(season_start) FROM bt_lab_matches;

/** Football matches in bt_lab_matches (db/062). */
export const LAB_FOOTBALL_MATCHES = 107_041

/** NBA games in bt_nba (db/027), 2014-15 to 2021-22. */
export const LAB_NBA_GAMES = 10_006

export const LAB_TOTAL_GAMES = LAB_FOOTBALL_MATCHES + LAB_NBA_GAMES

/** Football season start years covered: 2012-13 to 2026-27. */
export const LAB_FOOTBALL_SEASON_MIN = 2012
export const LAB_FOOTBALL_SEASON_MAX = 2026

export const LAB_SEASONS_TEXT = `${LAB_FOOTBALL_SEASON_MIN}–${LAB_FOOTBALL_SEASON_MAX + 1}`

/** "118,046" — the only way these numbers are printed. */
export function labCount(n: number): string {
  return n.toLocaleString('en-US')
}
