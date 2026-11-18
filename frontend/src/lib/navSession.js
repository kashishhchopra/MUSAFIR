// Per-session navigation memory (recently visited screens). Lives in
// sessionStorage -- it dies with the tab -- and is wiped on logout so the next
// person on the device never sees where the previous one had been. It stores
// only screen names (tab keys), never any personal data.
const PREFIX = 'musafir.nav.'
const key = (tid) => `${PREFIX}recents.${tid}`

export function readRecents(tid) {
  try {
    const v = JSON.parse(sessionStorage.getItem(key(tid)) || '[]')
    return Array.isArray(v) ? v.filter((r) => r && typeof r.tab === 'string') : []
  } catch { return [] }
}

export function writeRecents(tid, list) {
  try { sessionStorage.setItem(key(tid), JSON.stringify(list)) } catch { /* storage unavailable: memory only */ }
}

export function clearNavSession() {
  try {
    Object.keys(sessionStorage).filter((k) => k.startsWith(PREFIX)).forEach((k) => sessionStorage.removeItem(k))
  } catch { /* ignore */ }
}
