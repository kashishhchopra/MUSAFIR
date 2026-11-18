import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import '../lib/walkHomeStrings'

const INPUT = 'w-full border border-slate-300 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100 rounded-lg px-3 py-2 text-sm'
const errText = (t, e) => (e === 'offline' ? t('walk_home.offline_action') : e === 'generic' ? t('walk_home.generic_error') : e)

// Trusted contacts: pick who may follow THIS journey, add, edit and remove
// people. The server validates the phone/email, refuses duplicates and says why;
// that message is shown beside the form, not swallowed.
export default function WalkHomeContacts({ walk, picked, onToggle, onAdded, onRemoved }) {
  const { t } = useTranslation()
  const [name, setName] = useState('')
  const [contact, setContact] = useState('')
  const [err, setErr] = useState('')
  const [editing, setEditing] = useState(null) // {id, name, contact}
  const [editErr, setEditErr] = useState('')
  const [removing, setRemoving] = useState(null)
  const [working, setWorking] = useState(false)

  const add = async (e) => {
    e.preventDefault()
    if (!name.trim() || working) return
    setWorking(true)
    setErr('')
    const r = await walk.createGuardian(name.trim(), contact.trim())
    setWorking(false)
    if (r.ok) { setName(''); setContact(''); onAdded?.(r.guardian.id) } else setErr(errText(t, r.error))
  }

  const save = async () => {
    setWorking(true)
    setEditErr('')
    const r = await walk.editGuardian(editing.id, editing.name.trim(), editing.contact.trim())
    setWorking(false)
    if (r.ok) setEditing(null); else setEditErr(errText(t, r.error))
  }

  const remove = async (id) => {
    setWorking(true)
    const r = await walk.deleteGuardian(id)
    setWorking(false)
    setRemoving(null)
    if (r.ok) onRemoved?.(id)
  }

  return (
    <fieldset data-testid="wh-contacts">
      <legend className="text-xs font-semibold text-slate-600 dark:text-slate-300 mb-1">{t('walk_home.contacts_title')}</legend>
      {walk.guardians.length === 0 && <p className="text-xs text-slate-500">{t('walk_home.guardians_none')}</p>}
      <ul className="space-y-2">
        {walk.guardians.map((g) => (
          <li key={g.id} className="text-sm" data-testid="wh-contact">
            {editing?.id === g.id ? (
              <div className="space-y-1.5">
                <input value={editing.name} aria-label={t('walk_home.guardian_name')}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })} className={INPUT} />
                <input value={editing.contact} type="tel" aria-label={t('walk_home.contact_phone')}
                  placeholder={t('walk_home.contact_phone')}
                  onChange={(e) => setEditing({ ...editing, contact: e.target.value })} className={INPUT} />
                {editErr && <p role="alert" className="text-xs text-red-600">{editErr}</p>}
                <div className="flex gap-2">
                  <button type="button" onClick={save} disabled={working || !editing.name.trim()}
                    className="flex-1 text-xs font-semibold py-1.5 rounded-lg bg-sky-600 text-white disabled:opacity-50">
                    {t('walk_home.contact_save')}</button>
                  <button type="button" onClick={() => { setEditing(null); setEditErr('') }}
                    className="flex-1 text-xs font-semibold py-1.5 rounded-lg border border-slate-300 dark:border-slate-600">
                    {t('walk_home.contact_cancel')}</button>
                </div>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-2 min-w-0 flex-1">
                  <input type="checkbox" checked={picked.includes(g.id)} onChange={() => onToggle(g.id)} />
                  <span className="min-w-0">
                    <span className="block truncate">{g.guardian_name}</span>
                    <span className="block text-[11px] text-slate-500 truncate">
                      {g.guardian_contact || t('walk_home.contact_no_phone')}</span>
                  </span>
                </label>
                <button type="button" onClick={() => setEditing({ id: g.id, name: g.guardian_name, contact: g.guardian_contact || '' })}
                  className="text-xs font-semibold text-sky-700 dark:text-sky-400">{t('walk_home.contact_edit')}</button>
                {removing === g.id ? (
                  <button type="button" onClick={() => remove(g.id)} disabled={working} data-testid="wh-contact-remove-yes"
                    className="text-xs font-semibold text-red-600" title={t('walk_home.contact_remove_confirm')}>
                    {t('walk_home.confirm_yes')}</button>
                ) : (
                  <button type="button" onClick={() => setRemoving(g.id)} data-testid="wh-contact-remove"
                    className="text-xs font-semibold text-red-600">{t('walk_home.contact_remove')}</button>
                )}
              </div>
            )}
            {removing === g.id && <p className="text-[11px] text-slate-500 mt-0.5">{t('walk_home.contact_remove_confirm')}</p>}
          </li>
        ))}
      </ul>
      <p className="text-[11px] text-slate-500 mt-1">{t('walk_home.guardians_hint')}</p>
      <form onSubmit={add} className="mt-2 flex flex-col gap-2" noValidate>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('walk_home.guardian_name')}
          aria-label={t('walk_home.guardian_name')} maxLength={100} className={INPUT} />
        <input value={contact} onChange={(e) => setContact(e.target.value)} type="tel"
          placeholder={t('walk_home.contact_phone')} aria-label={t('walk_home.contact_phone')} className={INPUT} />
        {err && <p role="alert" data-testid="wh-contact-error" className="text-xs text-red-600">{err}</p>}
        <button disabled={!name.trim() || working}
          className="text-xs font-semibold py-2 rounded-lg bg-slate-100 dark:bg-slate-700 disabled:opacity-50">
          {t('walk_home.add_guardian')}
        </button>
      </form>
    </fieldset>
  )
}
