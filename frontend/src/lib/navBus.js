// A tiny registry that lets "Back" and "Home" know about everything that is open
// on top of the current screen (bottom sheets, chat panels, the recent-screens
// panel) without those components knowing about each other or about routing.
//
//  * Back handlers are tried most-recently-registered first (LIFO), so Back closes
//    the topmost thing. The screen-history handler registers first (at the bottom),
//    so it only runs when no overlay claims the press. A handler returns true when
//    it handled the press.
//  * Closers are called all at once by Home, so it clears overlays without
//    touching data, sessions or anything running in the background.
const backHandlers = []
const closers = new Set()

export function registerBack(fn) {
  backHandlers.push(fn)
  return () => {
    const i = backHandlers.lastIndexOf(fn)
    if (i >= 0) backHandlers.splice(i, 1)
  }
}

// -> true when something handled the press (an overlay closed, or the screen changed).
export function runBack() {
  for (let i = backHandlers.length - 1; i >= 0; i -= 1) {
    if (backHandlers[i]()) return true
  }
  return false
}

export function registerCloser(fn) {
  closers.add(fn)
  return () => closers.delete(fn)
}

export function closeAllOverlays() {
  ;[...closers].forEach((fn) => fn())
}
