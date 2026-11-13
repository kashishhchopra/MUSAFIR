import { Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../../auth.jsx'
import ThemeToggle from '../../components/ThemeToggle.jsx'
import PushToggle from '../../components/PushToggle.jsx'

// Lightweight nav shell for a field responder -- just enough chrome to show
// who's logged in and let them sign out. Not the full admin control-room
// layout (no multi-page nav: a responder only ever sees their own worklist).
export default function ResponderLayout() {
  const { user, logout } = useAuth()
  const nav = useNavigate()
  return (
    <div data-role-theme="police" className="theme-surface min-h-screen flex flex-col">
      <header className="bg-slate-900 text-white px-4 py-3 flex items-center justify-between border-b border-sky-900/60">
        <div className="flex items-center gap-2">
          <span className="text-xl">🚓</span>
          <span className="font-bold tracking-tight">Responder Console</span>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-slate-300 hidden sm:inline">{user?.full_name}</span>
          <ThemeToggle />
          <button onClick={() => { logout(); nav('/login') }}
            className="bg-slate-700 hover:bg-slate-600 px-3 py-1 rounded-lg">Logout</button>
        </div>
      </header>

      <main className="flex-1 p-4 max-w-[900px] w-full mx-auto">
        <div className="mb-3 rounded-xl bg-white dark:bg-slate-800 shadow-sm p-3">
          <div className="text-xs text-slate-500 mb-1">Dispatch alerts for your unit, even when this console is closed</div>
          <PushToggle />
        </div>
        <Outlet />
      </main>
    </div>
  )
}
