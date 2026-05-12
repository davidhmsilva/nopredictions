/**
 * Dixon-Coles model prediction — TypeScript port.
 * Reads pre-trained parameters from dc_params.json and computes
 * 1X2 + Over/Under + BTTS probabilities for any known team pair.
 */

import rawParams from './dc_params.json'

interface DCParams {
  teams: string[]
  attack: number[]
  defense: number[]
  home_adv: number
  rho: number
}

export interface DCPrediction {
  home_win: number
  draw: number
  away_win: number
  over_2_5: number
  under_2_5: number
  over_1_5: number
  under_1_5: number
  btts: number
  lambda_home: number
  lambda_away: number
}

const params = rawParams as DCParams

// Build normalised lookup: normalised_name → team index
const LOG2 = Math.log(2)

function norm(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, '')
    .replace(/\b(fc|cf|sc|ac|ss|afc|bsc|rcd|ssc|cd|rc|sl|as|ca|aa|fk|sk|rb|vfb|vfl|sv|bv)\b/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

// Explicit PM name → canonical model name aliases
const RAW_ALIASES: Record<string, string> = {
  "deportivo alaves": "Alaves", "alaves": "Alaves",
  "heart of midlothian": "Hearts", "hearts fc": "Hearts",
  "cordoba cf": "Cordoba",
  "cd castellon": "Castellon", "castellon": "Castellon",
  "cadiz cf": "Cadiz", "cadiz": "Cadiz",
  "ss lazio": "Lazio",
  "fc internazionale milano": "Inter", "internazionale": "Inter", "inter milan": "Inter",
  "inter miami": "__NOT_IN_MODEL__", "inter miami cf": "__NOT_IN_MODEL__",
  "stade brestois 29": "Brest", "stade brestois": "Brest",
  "stade rennais fc": "Rennes", "stade rennais": "Rennes",
  "racing club de lens": "Lens", "rc lens": "Lens",
  "ssc bari": "Bari",
  "cd guadalajara": "Guadalajara",
  "albacete balompie": "Albacete",
  "fc barcelona": "Barcelona",
  "real madrid cf": "Real Madrid",
  "atletico madrid": "Ath Madrid", "atletico de madrid": "Ath Madrid",
  "rcd espanyol de barcelona": "Espanyol", "rcd espanyol": "Espanyol",
  "real sociedad de futbol": "Real Sociedad", "real sociedad futbol": "Real Sociedad",
  "fk shakhtar donetsk": "Shakhtar Donetsk", "shakhtar": "Shakhtar Donetsk",
  "sk slavia praha": "Slavia Prague", "slavia praha": "Slavia Prague",
  "ac milan": "Milan",
  "hellas verona": "Verona",
  "us lecce": "Lecce",
  "atalanta bc": "Atalanta",
  "olympique de marseille": "Marseille",
  "olympique lyonnais": "Lyon",
  "paris saint-germain": "Paris SG", "paris saint germain": "Paris SG", "psg": "Paris SG",
  "as monaco": "Monaco",
  "ogc nice": "Nice",
  "rc strasbourg alsace": "Strasbourg", "rc strasbourg": "Strasbourg",
  "borussia dortmund": "Dortmund",
  "borussia monchengladbach": "Monchengladbach",
  "eintracht frankfurt": "Frankfurt",
  "bayer leverkusen": "Leverkusen",
  "tsg hoffenheim": "Hoffenheim", "tsg 1899 hoffenheim": "Hoffenheim",
  "1 fc union berlin": "Union Berlin", "fc union berlin": "Union Berlin",
  "sv werder bremen": "Werder Bremen",
  "fc schalke 04": "Schalke 04", "schalke": "Schalke 04",
  "hamburger sv": "Hamburg",
  "fsv mainz 05": "Mainz", "mainz 05": "Mainz",
  "1 fc heidenheim": "Heidenheim",
  "sporting cp": "Sp Lisbon", "sporting lisbon": "Sp Lisbon", "sporting clube de portugal": "Sp Lisbon",
  "sl benfica": "Benfica",
  "heart of midlothian fc": "Hearts",
  "real betis balompie": "Betis", "real betis": "Betis",
  "rc celta de vigo": "Celta", "celta vigo": "Celta",
  "sevilla fc": "Sevilla",
  "valencia cf": "Valencia",
  "villarreal cf": "Villarreal",
  "athletic club": "Athletic Bilbao", "athletic bilbao": "Athletic Bilbao",
  "ca osasuna": "Osasuna",
  "getafe cf": "Getafe",
  "tottenham hotspur": "Tottenham",
  "manchester united": "Man United",
  "manchester city": "Man City",
  "newcastle united": "Newcastle",
  "brighton & hove albion": "Brighton", "brighton and hove albion": "Brighton",
  "west ham united": "West Ham",
  "wolverhampton wanderers": "Wolves", "wolverhampton": "Wolves",
  "nottingham forest": "Nottm Forest",
  "ajax amsterdam": "Ajax",
  "psv eindhoven": "PSV",
  "feyenoord rotterdam": "Feyenoord",
}

// Normalise alias keys at module load time
const ALIASES: Map<string, string> = new Map()
for (const [k, v] of Object.entries(RAW_ALIASES)) {
  ALIASES.set(norm(k), v)
}

// Build a normalised → index map once at module load
const normIdx: Map<string, number> = new Map()
for (let i = 0; i < params.teams.length; i++) {
  normIdx.set(norm(params.teams[i]), i)
}

function findTeam(name: string): number | null {
  const n = norm(name)

  // 1. Alias table
  const canonical = ALIASES.get(n)
  if (canonical !== undefined) {
    if (canonical === '__NOT_IN_MODEL__') return null
    const cn = norm(canonical)
    const aliasIdx = normIdx.get(cn)
    if (aliasIdx !== undefined) return aliasIdx
  }

  // 2. Exact normalised match
  const exact = normIdx.get(n)
  if (exact !== undefined) return exact

  // 3. Prefix match (first 7 chars, min length 5)
  const pre = n.slice(0, 7)
  if (pre.length >= 5) {
    for (const [key, idx] of normIdx) {
      if (key.length >= 5 && (key.startsWith(pre) || pre.startsWith(key.slice(0, 7)))) return idx
    }
  }

  // 4. Substring — only for long names (≥7 chars) to avoid false positives
  if (n.length >= 7) {
    for (const [key, idx] of normIdx) {
      if (key.length >= 7 && (key.includes(n) || n.includes(key))) return idx
    }
  }

  return null
}

// Poisson PMF
function poissonPMF(k: number, lambda: number): number {
  if (lambda <= 0) return k === 0 ? 1 : 0
  let logP = k * Math.log(lambda) - lambda
  for (let i = 1; i <= k; i++) logP -= Math.log(i)
  return Math.exp(logP)
}

// Dixon-Coles τ correction for low scores
function tau(x: number, y: number, lh: number, la: number, rho: number): number {
  if (x === 0 && y === 0) return 1 - lh * la * rho
  if (x === 1 && y === 0) return 1 + la * rho
  if (x === 0 && y === 1) return 1 + lh * rho
  if (x === 1 && y === 1) return 1 - rho
  return 1
}

export function dcPredict(homeTeam: string, awayTeam: string): DCPrediction | null {
  const h = findTeam(homeTeam)
  const a = findTeam(awayTeam)
  if (h === null || a === null) return null

  const lh = Math.exp(params.attack[h] + params.defense[a] + params.home_adv)
  const la = Math.exp(params.attack[a] + params.defense[h])
  const rho = params.rho
  const MAX = 8

  // Build score probability matrix
  const matrix: number[][] = Array.from({ length: MAX + 1 }, () => new Array(MAX + 1).fill(0))
  for (let i = 0; i <= MAX; i++) {
    for (let j = 0; j <= MAX; j++) {
      const t = tau(i, j, lh, la, rho)
      matrix[i][j] = Math.max(0, poissonPMF(i, lh) * poissonPMF(j, la) * t)
    }
  }

  // Normalise
  let total = 0
  for (let i = 0; i <= MAX; i++) for (let j = 0; j <= MAX; j++) total += matrix[i][j]
  if (total <= 0) return null
  for (let i = 0; i <= MAX; i++) for (let j = 0; j <= MAX; j++) matrix[i][j] /= total

  let homeWin = 0, draw = 0, awayWin = 0
  let over25 = 0, under25 = 0, over15 = 0, under15 = 0, btts = 0

  for (let i = 0; i <= MAX; i++) {
    for (let j = 0; j <= MAX; j++) {
      const p = matrix[i][j]
      if (i > j) homeWin += p
      else if (i === j) draw += p
      else awayWin += p
      const g = i + j
      if (g > 2.5) over25 += p; else under25 += p
      if (g > 1.5) over15 += p; else under15 += p
      if (i >= 1 && j >= 1) btts += p
    }
  }

  return {
    home_win: homeWin,
    draw,
    away_win: awayWin,
    over_2_5: over25,
    under_2_5: under25,
    over_1_5: over15,
    under_1_5: under15,
    btts,
    lambda_home: lh,
    lambda_away: la,
  }
}

export const DC_FIT_DATE: string = (rawParams as { fit_date: string }).fit_date
export const DC_N_MATCHES: number = (rawParams as { n_matches: number }).n_matches
