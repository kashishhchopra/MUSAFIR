import { useState } from 'react'
import ResponseBoard from '../../components/ResponseBoard.jsx'
import ResponseSlo from '../../components/ResponseSlo.jsx'
import ResponseSettings from '../../components/ResponseSettings.jsx'

const TABS = [
  { id: 'board', label: '⏱ Live response board' },
  { id: 'slo', label: '📈 Response performance (SLO)' },
  { id: 'settings', label: '⚙️ Targets & escalation' },
]

// Admin "Response & SLO": live countdowns, response-performance analytics and the
// configuration of targets/escalation. All data is served by /api/response/*.
export default function ResponseCenter() {
  const [tab, setTab] = useState('board')
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-bold text-slate-800 dark:text-slate-100">Response &amp; SLO</h2>
      <div role="tablist" className="flex gap-1 overflow-x-auto">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
            className={`px-3 py-1.5 rounded-lg text-sm whitespace-nowrap ${tab === t.id
              ? 'bg-sky-600 text-white' : 'bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700'}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'board' && <ResponseBoard isAdmin />}
      {tab === 'slo' && <ResponseSlo />}
      {tab === 'settings' && <ResponseSettings />}
    </div>
  )
}
