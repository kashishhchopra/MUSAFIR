import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from './auth.jsx'
import { RTL_LANGUAGES } from './i18n.js'
import Login from './pages/Login.jsx'
import Register from './pages/Register.jsx'
import ForgotPassword from './pages/ForgotPassword.jsx'
import GuardianView from './pages/GuardianView.jsx'
import AdminLayout from './pages/admin/AdminLayout.jsx'
import Dashboard from './pages/admin/Dashboard.jsx'
import TouristSearch from './pages/admin/TouristSearch.jsx'
import Zones from './pages/admin/Zones.jsx'
import Incidents from './pages/admin/Incidents.jsx'
import PoliceNetwork from './pages/admin/PoliceNetwork.jsx'
import LiveEmergencies from './pages/admin/LiveEmergencies.jsx'
import Analytics from './pages/admin/Analytics.jsx'
import ResponseCenter from './pages/admin/ResponseCenter.jsx'
import Devices from './pages/admin/Devices.jsx'
import Simulation from './pages/admin/Simulation.jsx'
import Notifications from './pages/admin/Notifications.jsx'
import AuditLog from './pages/admin/AuditLog.jsx'
import PrivacyAdmin from './pages/admin/PrivacyAdmin.jsx'
import TouristApp from './pages/tourist/TouristApp.jsx'
import ResponderLayout from './pages/responder/ResponderLayout.jsx'
import ResponderConsole from './pages/responder/ResponderConsole.jsx'
import NativeBridge from './components/NativeBridge.jsx'
import { isNativeApp } from './lib/platform'

function Protected({ role, children }) {
  const { user, ready } = useAuth()
  // While the silent refresh against the httpOnly cookie is in flight (see
  // auth.jsx), a rehydrated `user` is only a hint -- rendering the
  // redirect-to-login too early would flash a logged-out screen on every
  // hard reload even for a valid session.
  if (!ready) return null
  if (!user) return <Navigate to="/login" replace />
  const allowed = Array.isArray(role) ? role.includes(user.role) : user.role === role
  if (role && !allowed) return <Navigate to="/" replace />
  return children
}

// The app opens straight into the login screen -- no marketing/landing page
// in front of it. A logged-in user hitting "/" (or anything unmatched, via
// the catch-all route below) still lands on their own console.
function Home() {
  const { user } = useAuth()
  if (!user) return <Navigate to="/login" replace />
  if (isNativeApp() && user.role !== 'tourist') return <WebOnlyNotice />
  if (user.role === 'admin') return <Navigate to="/admin" replace />
  if (user.role === 'responder') return <Navigate to="/responder" replace />
  return <Navigate to="/app" replace />
}

// The Android app is the tourist app only; staff consoles stay on the web.
function WebOnlyNotice() {
  const { logout } = useAuth()
  return (
    <div className="min-h-screen flex items-center justify-center p-6 bg-slate-100 dark:bg-slate-900">
      <div className="max-w-sm text-center bg-white dark:bg-slate-800 rounded-2xl shadow-xl p-6">
        <p className="font-semibold text-slate-800 dark:text-slate-100">This app is for tourists.</p>
        <p className="text-sm text-slate-600 dark:text-slate-300 mt-2">
          Admin and responder consoles are available on the web dashboard.
        </p>
        <button onClick={logout} className="mt-4 px-4 py-2 rounded-lg bg-sky-600 text-white text-sm font-semibold">
          Log out
        </button>
      </div>
    </div>
  )
}

// RTL only applies to the tourist-facing screens (this app's translated
// surface) -- the admin/responder consoles are untranslated and used by
// Indian police operators, so flipping their layout would be actively
// wrong, not just unnecessary. See i18n.js:RTL_LANGUAGES and i18n.rtl.test.js.
function useDocumentDirection() {
  const { i18n } = useTranslation()
  const location = useLocation()

  useEffect(() => {
    const lang = i18n.resolvedLanguage || i18n.language
    const isAdminRoute = location.pathname.startsWith('/admin') || location.pathname.startsWith('/responder')
    const rtl = RTL_LANGUAGES.includes(lang) && !isAdminRoute
    document.documentElement.dir = rtl ? 'rtl' : 'ltr'
    document.documentElement.lang = lang
  }, [i18n.resolvedLanguage, i18n.language, location.pathname])
}

export default function App() {
  useDocumentDirection()
  const native = isNativeApp()
  return (
    <>
      {native && <NativeBridge />}
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/" element={<Home />} />
        <Route path="/app" element={<Protected role="tourist"><TouristApp /></Protected>} />

        {!native && (
          <>
            <Route path="/guardian/:token" element={<GuardianView />} />
            <Route path="/admin" element={<Protected role="admin"><AdminLayout /></Protected>}>
              <Route index element={<Dashboard />} />
              <Route path="tourists" element={<TouristSearch />} />
              <Route path="zones" element={<Zones />} />
              <Route path="incidents" element={<Incidents />} />
              <Route path="police-network" element={<PoliceNetwork />} />
              <Route path="live-emergencies" element={<LiveEmergencies />} />
              <Route path="analytics" element={<Analytics />} />
              <Route path="response" element={<ResponseCenter />} />
              <Route path="devices" element={<Devices />} />
              <Route path="simulation" element={<Simulation />} />
              <Route path="notifications" element={<Notifications />} />
              <Route path="audit" element={<AuditLog />} />
              <Route path="privacy" element={<PrivacyAdmin />} />
            </Route>
            <Route path="/responder" element={<Protected role="responder"><ResponderLayout /></Protected>}>
              <Route index element={<ResponderConsole />} />
            </Route>
          </>
        )}

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  )
}
