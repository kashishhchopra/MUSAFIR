import { useEffect, useState } from 'react'
import useQrCamera from '../hooks/useQrCamera'
import { decodeQrFromFile } from '../lib/qrImage'
import MedicalProfileView from './MedicalProfileView.jsx'
import {
  ACCESS_STATUS_TEXT, accessMedicalProfile, errorText, getMedicalOptions, purposeLabel,
} from '../lib/medicalProfileService'

// "Scan Tourist Medical QR": camera (jsQR) or pasted token -> POST
// /tourist-id/medical. The browser only forwards what it read; the backend
// decides whether the token is valid and whether THIS logged-in account may
// see medical data. Nothing medical is rendered unless status === 'granted'.
export default function MedicalQrScanner() {
  const [phase, setPhase] = useState('idle') // idle | loading | done
  const [result, setResult] = useState(null) // granted payload
  const [problem, setProblem] = useState(null) // { kind, text }
  const [manual, setManual] = useState('')
  const [opts, setOpts] = useState(null)
  const [purpose, setPurpose] = useState('')

  useEffect(() => {
    getMedicalOptions().then((o) => {
      setOpts(o)
      if (o.access_purposes?.[0]) setPurpose(o.access_purposes[0])
    }).catch(() => {})
  }, [])

  const lookup = async (token) => {
    setPhase('loading')
    setProblem(null)
    setResult(null)
    try {
      const data = await accessMedicalProfile({ token, purpose: purpose || undefined })
      if (data.status === 'granted') {
        setResult(data)
      } else {
        setProblem({
          kind: data.status,
          text: data.reason || ACCESS_STATUS_TEXT[data.status] || 'This QR code could not be used.',
          tourist: data.status === 'no_profile' ? data.tourist : null,
        })
      }
    } catch (e) {
      const code = e?.response?.status
      if (code === 403) setProblem({ kind: 'unauthorized', text: 'Your account is not authorized to view medical profiles.' })
      else if (code === 429) setProblem({ kind: 'throttled', text: errorText(e, 'Too many attempts. Wait a few minutes and try again.') })
      else if (code === 400 || code === 422) setProblem({ kind: 'bad_request', text: errorText(e) })
      else setProblem({ kind: 'network', text: 'Could not reach the server. Check your connection and try again.' })
    } finally {
      setPhase('done')
    }
  }

  const cam = useQrCamera((text) => lookup(text))

  const onImage = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = '' // allow choosing the same file again
    if (!file) return
    setProblem(null)
    try {
      const text = await decodeQrFromFile(file)
      if (text) lookup(text)
      else setProblem({ kind: 'invalid', text: 'No QR code could be read from that image. Try a clearer, closer picture.' })
    } catch {
      setProblem({ kind: 'invalid', text: 'That file could not be read as an image.' })
    }
  }

  const reset = () => {
    setResult(null)
    setProblem(null)
    setManual('')
    setPhase('idle')
  }

  if (result) return <MedicalProfileView result={result} onBack={reset} />

  const busy = phase === 'loading'
  return (
    <div className="space-y-3" data-testid="medical-scanner">
      {opts?.access_purposes?.length > 0 && (
        <label className="block text-xs font-semibold text-slate-600 dark:text-slate-300">
          Reason for access{opts.access_purpose_required ? ' (required)' : ''}
          <select value={purpose} onChange={(e) => setPurpose(e.target.value)} aria-label="Reason for access"
            className="mt-1 w-full border border-slate-300 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-3 py-2 text-sm">
            {!opts.access_purpose_required && <option value="">Not specified</option>}
            {opts.access_purposes.map((p) => <option key={p} value={p}>{purposeLabel(p)}</option>)}
          </select>
        </label>
      )}

      <div className="relative bg-black rounded-xl overflow-hidden" style={{ aspectRatio: '4/3' }}>
        <video ref={cam.videoRef} muted playsInline aria-label="Camera preview" className="w-full h-full object-cover opacity-90" />
        {!cam.active && (
          <div className="absolute inset-0 flex items-center justify-center p-4">
            <button onClick={cam.start} disabled={busy}
              className="bg-sky-600 hover:bg-sky-700 disabled:opacity-50 text-white font-semibold rounded-xl px-5 py-3">
              📷 Start camera
            </button>
          </div>
        )}
        {cam.active && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="w-2/3 aspect-square border-2 border-sky-400/80 rounded-xl" />
          </div>
        )}
      </div>
      {cam.error === 'denied' && (
        <div role="alert" className="text-sm text-orange-700 dark:text-orange-300">
          Camera permission was denied. Allow camera access in your browser, or paste the code below.
        </div>
      )}
      {cam.error === 'unsupported' && (
        <div role="alert" className="text-sm text-orange-700 dark:text-orange-300">
          Camera is not available on this device. Paste the code below.
        </div>
      )}

      <label className="block text-center text-sm font-semibold bg-slate-100 dark:bg-slate-700 text-slate-800 dark:text-slate-100 rounded-xl py-2.5 cursor-pointer">
        🖼 Upload a QR image
        <input type="file" accept="image/*" onChange={onImage} aria-label="Upload QR image" className="sr-only" />
      </label>

      <form onSubmit={(e) => { e.preventDefault(); if (manual.trim()) lookup(manual.trim()) }} className="flex gap-2">
        <input value={manual} onChange={(e) => setManual(e.target.value)} aria-label="QR code text"
          placeholder="Or paste QR code text (TSID:…)" autoComplete="off" spellCheck={false}
          className="flex-1 min-w-0 border border-slate-300 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-3 py-2 text-sm font-mono" />
        <button type="submit" disabled={!manual.trim() || busy}
          className="bg-sky-600 hover:bg-sky-700 disabled:opacity-40 text-white text-sm font-semibold px-4 rounded-lg">
          Open
        </button>
      </form>

      {busy && <div role="status" className="text-center text-sm text-slate-500 py-2">Checking access…</div>}

      {problem && (
        <div role="alert" data-testid="scan-problem" data-kind={problem.kind}
          className="rounded-xl border-2 border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/40 p-3 text-sm text-red-800 dark:text-red-200">
          <div className="font-bold">
            {{ invalid: 'Invalid QR code', revoked: 'QR code revoked', expired: 'QR code expired',
              no_profile: 'No medical profile', unauthorized: 'Not authorized', throttled: 'Too many attempts',
              network: 'Connection problem', bad_request: 'Request not accepted' }[problem.kind] || 'Could not open'}
          </div>
          <div>{problem.text}</div>
          {problem.tourist && (
            <div className="mt-1 text-slate-700 dark:text-slate-200">
              {problem.tourist.full_name} · <span className="font-mono">{problem.tourist.digital_id}</span>
            </div>
          )}
          <button onClick={reset} className="mt-2 text-xs font-semibold underline">Try again</button>
        </div>
      )}
    </div>
  )
}
