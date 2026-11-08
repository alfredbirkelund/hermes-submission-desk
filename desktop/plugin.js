// Submission Desk: the Desktop half (a page under the sidebar, plus a ⌘K command).
//
// Everything here is the applicant's surface: decisions made on this page are recorded as the
// applicant's own (the backend stamps them `human`/`desktop`). The page reads readiness from the
// plugin backend (`/api/plugins/submission-desk`), which re-reads and hashes the mapped files on
// every recheck. Nothing on this page uploads, signs or submits anything.
//
// Loaded uncompiled: no JSX, only `@hermes/plugin-sdk`, `react` and `react/jsx-runtime` imports.
// Layout uses inline styles with the app's theme variables, because Tailwind classes that the app
// itself never uses do not exist in its stylesheet.

import {
  Badge,
  Button,
  Checkbox,
  Codicon,
  ConfirmDialog,
  CopyButton,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Loader,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  Textarea,
  host,
  useQuery,
  useQueryClient
} from '@hermes/plugin-sdk'
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'

const ID = 'submission-desk'
const PATH = '/submission-desk'
const h = React.createElement
let rest = null // ctx.rest, bound in register()
let storage = null

// ───────────────────────────────────────────── backend calls

function parseError(err) {
  // Desktop's REST bridge rejects with "<status>: <body>". When the rejection crosses Electron IPC
  // the message arrives wrapped: "Error invoking remote method 'hermes:api': Error: 409: {...}".
  const raw = String((err && err.message) || err || 'Request failed')
  const text = raw.replace(/^Error invoking remote method '[^']*':\s*/, '').replace(/^Error:\s*/, '')
  const match = /^(\d{3}):\s*([\s\S]*)$/.exec(text)
  const status = match ? Number(match[1]) : Number(err && err.statusCode) || 0
  let body = null
  try {
    body = match ? JSON.parse(match[2]) : null
  } catch {
    body = null
  }
  if (body && typeof body.detail === 'object' && body.detail && !body.error) body = body.detail // FastAPI HTTPException shape
  return {
    status,
    code: (body && body.error) || (status ? 'http_' + status : 'network'),
    message: (body && body.message) || (match ? match[2] : text) || 'Request failed',
    hint: (body && body.hint) || '',
    details: (body && body.details) || {}
  }
}

async function api(path, options) {
  if (!rest) throw { status: 0, code: 'unavailable', message: 'Submission Desk backend is not connected.', hint: '' }
  try {
    return await rest(path, options || {})
  } catch (err) {
    throw parseError(err)
  }
}

function newKey() {
  try {
    return crypto.randomUUID()
  } catch {
    return 'k-' + Date.now() + '-' + Math.random().toString(16).slice(2)
  }
}

// ───────────────────────────────────────────── presentation helpers

const S = {
  page: { display: 'flex', flexDirection: 'column', gap: 16, padding: '20px 24px 48px', maxWidth: 1100, margin: '0 auto', width: '100%', boxSizing: 'border-box' },
  row: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', minWidth: 0 },
  col: { display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 },
  muted: { color: 'var(--ui-text-tertiary)' },
  faint: { color: 'var(--ui-text-quaternary)' },
  small: { fontSize: 12, lineHeight: '18px' },
  tiny: { fontSize: 11, lineHeight: '16px' },
  mono: { fontFamily: 'var(--font-mono)', fontSize: 11.5, wordBreak: 'break-all' },
  section: { display: 'flex', flexDirection: 'column', gap: 8 },
  sectionTitle: { fontSize: 13, fontWeight: 600, color: 'var(--ui-text-secondary)', margin: 0 },
  card: { border: '1px solid var(--ui-stroke-tertiary)', borderRadius: 6, padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 },
  quote: { borderLeft: '2px solid var(--ui-stroke-secondary)', paddingLeft: 10, color: 'var(--ui-text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' },
  label: { fontSize: 12, fontWeight: 500, color: 'var(--ui-text-secondary)' },
  error: { color: 'var(--ui-red)', fontSize: 12, lineHeight: '18px', whiteSpace: 'pre-wrap' },
  grid2: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 },
  sub: { border: '1px solid var(--ui-stroke-tertiary)', borderRadius: 6, padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0, background: 'color-mix(in srgb, var(--ui-text-secondary) 4%, transparent)' },
  kv: { display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '3px 10px', alignItems: 'baseline', minWidth: 0 },
  key: { fontSize: 10.5, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--ui-text-tertiary)' },
  next: { display: 'flex', gap: 8, alignItems: 'flex-start', padding: '7px 10px', borderRadius: 6, background: 'color-mix(in srgb, var(--ui-accent) 9%, transparent)', border: '1px solid color-mix(in srgb, var(--ui-accent) 30%, transparent)' },
  primaryText: { fontSize: 13.5, fontWeight: 500, color: 'var(--ui-text-primary)', wordBreak: 'break-word' }
}

const STATE = {
  conflicting: { label: 'Conflicting', variant: 'destructive', icon: 'git-compare' },
  failed: { label: 'Failed', variant: 'destructive', icon: 'error' },
  missing: { label: 'Missing', variant: 'destructive', icon: 'circle-slash' },
  stale: { label: 'Stale', variant: 'warn', icon: 'history' },
  unknown: { label: 'Unknown', variant: 'warn', icon: 'question' },
  awaiting_confirmation: { label: 'Awaiting your confirmation', variant: 'default', icon: 'comment-discussion' },
  awaiting_review: { label: 'Awaiting your review', variant: 'default', icon: 'eye' },
  human_action: { label: 'Your action', variant: 'default', icon: 'person' },
  ready: { label: 'No local blocker', variant: 'success', icon: 'pass' },
  excluded: { label: 'Excluded', variant: 'muted', icon: 'circle-slash' }
}
const CHECK_VARIANT = { pass: 'success', fail: 'destructive', unknown: 'warn', not_applicable: 'muted' }

function StateBadge({ state }) {
  const s = STATE[state] || { label: state, variant: 'muted', icon: 'circle' }
  return h(Badge, { variant: s.variant, title: s.label }, h(Codicon, { name: s.icon, size: 11 }), s.label)
}

function short(sha) {
  return sha ? String(sha).slice(0, 10) : ''
}

function bytes(n) {
  if (n === null || n === undefined) return ''
  return Number(n).toLocaleString() + ' B'
}

function timeLeft(hours) {
  if (typeof hours !== 'number' || isNaN(hours)) return 'time left unknown'
  if (hours < 0) return 'passed'
  if (hours < 48) return `${Math.max(0, Math.round(hours))} h left`
  return `${Math.floor(hours / 24)} days left`
}

function when(iso) {
  if (!iso) return 'never'
  const d = new Date(iso)
  return isNaN(d.getTime()) ? String(iso) : d.toLocaleString()
}

function ErrorLine({ error }) {
  if (!error) return null
  return h('div', { role: 'alert', style: S.error },
    error.code === 'stale_revision' ? 'Not saved: ' + error.message + ' Your input is kept; review the refreshed page and try again.' : error.message,
    error.hint ? h('div', { style: { ...S.muted, marginTop: 2 } }, error.hint) : null)
}

function Field({ label, hint, children }) {
  return h('label', { style: { ...S.col, gap: 4 } },
    h('span', { style: S.label }, label),
    children,
    hint ? h('span', { style: { ...S.tiny, ...S.muted } }, hint) : null)
}

function Section({ title, count, children, actions }) {
  return h('section', { style: S.section },
    h('div', { style: { ...S.row, justifyContent: 'space-between' } },
      h('h3', { style: S.sectionTitle }, title, count != null ? h('span', { style: { ...S.faint, fontWeight: 400 } }, ' · ' + count) : null),
      actions || null),
    children)
}

// ───────────────────────────────────────────── data hooks

const keys = {
  list: [ID, 'workspaces'],
  ws: id => [ID, 'ws', id],
  files: id => [ID, 'files', id]
}

function useWorkspaces() {
  return useQuery({ queryKey: keys.list, queryFn: () => api('/workspaces'), refetchInterval: 30000 })
}

function useReadiness(wsId) {
  return useQuery({
    queryKey: keys.ws(wsId),
    queryFn: () => api('/workspaces/' + encodeURIComponent(wsId)),
    enabled: !!wsId,
    refetchInterval: 15000 // picks up agent proposals; a plain read never re-hashes files
  })
}

// One mutation runner per dialog/row: keeps an idempotency key across retries, a busy flag and
// the last error. After any change it rechecks, so the page always shows current evidence.
function useRunner(wsId) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const keyRef = useRef(newKey())
  const run = useCallback(async (path, body, opts) => {
    const options = opts || {}
    setBusy(true)
    setError(null)
    try {
      const payload = { ...(body || {}), idempotency_key: keyRef.current }
      const out = await api('/workspaces/' + encodeURIComponent(wsId) + path, { method: 'POST', body: payload })
      keyRef.current = newKey() // the next submission is a new request
      if (options.recheck !== false) {
        const fresh = await api('/workspaces/' + encodeURIComponent(wsId) + '/check', { method: 'POST' })
        qc.setQueryData(keys.ws(wsId), fresh)
      } else {
        await qc.invalidateQueries({ queryKey: keys.ws(wsId) })
      }
      qc.invalidateQueries({ queryKey: keys.list })
      return out
    } catch (err) {
      setError(err)
      if (err && err.code === 'stale_revision') qc.invalidateQueries({ queryKey: keys.ws(wsId) })
      throw err
    } finally {
      setBusy(false)
    }
  }, [wsId, qc])
  return { run, busy, error, setError }
}

// ───────────────────────────────────────────── setup

function SetupPanel({ onCreated, compact }) {
  const qc = useQueryClient()
  const [title, setTitle] = useState('')
  const [folder, setFolder] = useState('')
  const [tz, setTz] = useState(() => {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || '' } catch { return '' }
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const keyRef = useRef(newKey())
  const submit = async event => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const out = await api('/workspaces', { method: 'POST', body: { title, material_root: folder, timezone: tz || null, idempotency_key: keyRef.current } })
      keyRef.current = newKey()
      await qc.invalidateQueries({ queryKey: keys.list })
      onCreated(out.workspace.id)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }
  return h('form', { onSubmit: submit, style: { ...S.card, gap: 12, maxWidth: 640 } },
    compact ? null : h('div', { style: S.col },
      h('div', { style: { fontSize: 15, fontWeight: 600 } }, 'Set up an application'),
      h('div', { style: { ...S.small, ...S.muted } },
        'One application per workspace: a grant, scholarship or university application. Pick the folder that holds its files. ',
        'Submission Desk reads files only inside that folder, never changes them, and never logs in, uploads, signs or submits.')),
    h(Field, { label: 'Application name' }, h(Input, { value: title, onChange: e => setTitle(e.target.value), placeholder: 'e.g. Example Foundation Fellowship 2027', required: true, autoFocus: true })),
    h(Field, { label: 'Material folder', hint: 'Absolute path on the machine running Hermes (for a remote backend, a path on that machine).' },
      h(Input, { value: folder, onChange: e => setFolder(e.target.value), placeholder: '/home/you/applications/fellowship-2027', required: true, spellCheck: false })),
    h(Field, { label: 'Your time zone', hint: 'Used to show deadlines. Deadlines without a time zone in the instructions stay unknown.' },
      h(Input, { value: tz, onChange: e => setTz(e.target.value), placeholder: 'Europe/Copenhagen', spellCheck: false })),
    h(ErrorLine, { error }),
    h('div', { style: S.row }, h(Button, { type: 'submit', disabled: busy || !title.trim() || !folder.trim() }, busy ? 'Creating…' : 'Create workspace')))
}

// ───────────────────────────────────────────── dialogs

function DeskDialog({ open, onOpenChange, title, description, children, footer, wide }) {
  return h(Dialog, { open, onOpenChange },
    h(DialogContent, { style: { maxWidth: wide ? 760 : 560, width: 'calc(100vw - 48px)', maxHeight: 'calc(100vh - 64px)', overflowY: 'auto' } },
      h(DialogHeader, null, h(DialogTitle, null, title), description ? h(DialogDescription, null, description) : null),
      h('div', { style: { ...S.col, gap: 12 } }, children),
      footer ? h(DialogFooter, null, footer) : null))
}

const KINDS = [
  ['document', 'File uploaded to a portal field'],
  ['text', 'Text pasted into a portal box (keep it in a .txt file)'],
  ['human_action', 'Something only you can do (sign, request a document)'],
  ['attestation', 'A statement only you can confirm (eligibility)'],
  ['deadline', 'Submission deadline'],
  ['other', 'Anything else (tracked, never machine-passed)']
]
const FORMATS = ['pdf', 'docx', 'txt', 'png', 'jpeg']

function rulesToForm(rules) {
  const r = rules || {}
  return {
    formats: r.formats ? r.formats.map(f => (f === 'text' ? 'txt' : f)) : [],
    max_size: r.max_size ? r.max_size.text : '',
    size_basis: r.max_size && ['decimal', 'binary'].includes(r.max_size.basis) ? r.max_size.basis : '',
    min_pages: r.min_pages != null ? String(r.min_pages) : '',
    max_pages: r.max_pages != null ? String(r.max_pages) : '',
    min_words: r.min_words != null ? String(r.min_words) : '',
    max_words: r.max_words != null ? String(r.max_words) : '',
    word_convention: r.word_convention || 'unresolved',
    word_exclusions: (r.word_exclusions || []).join(', '),
    forbid_encryption: !!r.forbid_encryption,
    deadline_at: r.deadline ? r.deadline.local : '',
    deadline_tz: r.deadline ? r.deadline.timezone || '' : ''
  }
}

function formToRules(f, kind) {
  const rules = {}
  if (kind === 'deadline') {
    if (f.deadline_at) rules.deadline = { at: f.deadline_at, timezone: f.deadline_tz || undefined }
    return rules
  }
  if (f.formats.length) rules.formats = f.formats
  if (f.max_size.trim()) rules.max_size = f.size_basis ? { ...splitSize(f.max_size), basis: f.size_basis } : f.max_size.trim()
  for (const key of ['min_pages', 'max_pages', 'min_words', 'max_words']) if (String(f[key]).trim()) rules[key] = String(f[key]).trim()
  if (rules.min_words || rules.max_words) {
    rules.word_convention = f.word_convention
    const ex = f.word_exclusions.split(',').map(s => s.trim()).filter(Boolean)
    if (ex.length) rules.word_exclusions = ex
  }
  if (f.forbid_encryption) rules.forbid_encryption = true
  return rules
}

function splitSize(text) {
  const m = /^\s*([0-9.,]+)\s*([A-Za-z]+)\s*$/.exec(text)
  return m ? { value: m[1], unit: m[2] } : { value: text, unit: '' }
}

function RulesEditor({ kind, form, setForm }) {
  const set = (key, value) => setForm(prev => ({ ...prev, [key]: value }))
  if (kind === 'deadline') {
    return h('div', { style: S.grid2 },
      h(Field, { label: 'Deadline (local date and time)' }, h(Input, { type: 'datetime-local', value: form.deadline_at, onChange: e => set('deadline_at', e.target.value) })),
      h(Field, { label: 'Time zone stated in the instructions', hint: 'IANA name. Leave empty if the instructions do not say; it then stays unknown.' },
        h(Input, { value: form.deadline_tz, onChange: e => set('deadline_tz', e.target.value), placeholder: 'Europe/Copenhagen', spellCheck: false })))
  }
  if (!['document', 'text'].includes(kind)) return null
  return h('div', { style: { ...S.col, gap: 10 } },
    h('div', { style: S.col },
      h('span', { style: S.label }, 'Accepted formats (checked from the file content, not the name)'),
      h('div', { style: S.row }, FORMATS.map(fmt => h('label', { key: fmt, style: { ...S.row, gap: 4, ...S.small } },
        h(Checkbox, { checked: form.formats.includes(fmt), onCheckedChange: on => set('formats', on ? [...form.formats, fmt] : form.formats.filter(x => x !== fmt)) }),
        fmt.toUpperCase())))),
    h('div', { style: S.grid2 },
      h(Field, { label: 'Max size', hint: 'e.g. 5 MB, 500 KB, 2 MiB. If the instructions do not say whether MB is 1,000,000 or 1,048,576 bytes, leave the basis open.' },
        h(Input, { value: form.max_size, onChange: e => set('max_size', e.target.value), placeholder: '5 MB' })),
      h(Field, { label: 'Size basis' },
        h('select', { value: form.size_basis, onChange: e => set('size_basis', e.target.value), style: nativeSelect },
          h('option', { value: '' }, 'Not stated (show both readings)'),
          h('option', { value: 'decimal' }, 'Decimal (1 MB = 1,000,000 B)'),
          h('option', { value: 'binary' }, 'Binary (1 MB = 1,048,576 B)'))),
      h(Field, { label: 'Min pages (PDF)' }, h(Input, { inputMode: 'numeric', value: form.min_pages, onChange: e => set('min_pages', e.target.value) })),
      h(Field, { label: 'Max pages (PDF)' }, h(Input, { inputMode: 'numeric', value: form.max_pages, onChange: e => set('max_pages', e.target.value) })),
      h(Field, { label: 'Min words' }, h(Input, { inputMode: 'numeric', value: form.min_words, onChange: e => set('min_words', e.target.value) })),
      h(Field, { label: 'Max words' }, h(Input, { inputMode: 'numeric', value: form.max_words, onChange: e => set('max_words', e.target.value) }))),
    (form.min_words || form.max_words) ? h('div', { style: S.grid2 },
      h(Field, { label: 'How the instructions count words', hint: 'Unless the instructions say, keep “not stated”: the count is then shown as an estimate, never certified.' },
        h('select', { value: form.word_convention, onChange: e => set('word_convention', e.target.value), style: nativeSelect },
          h('option', { value: 'unresolved' }, 'Not stated'),
          h('option', { value: 'whitespace_tokens' }, 'Every space-separated token'),
          h('option', { value: 'letters_digits_tokens' }, 'Tokens with a letter or digit'))),
      h(Field, { label: 'Excluded parts', hint: 'e.g. references, footnotes. These cannot be separated automatically, so the count becomes an estimate.' },
        h(Input, { value: form.word_exclusions, onChange: e => set('word_exclusions', e.target.value), placeholder: 'references' }))) : null,
    h('label', { style: { ...S.row, gap: 6, ...S.small } },
      h(Checkbox, { checked: form.forbid_encryption, onCheckedChange: on => set('forbid_encryption', !!on) }), 'The file must not be password-protected'))
}

const nativeSelect = {
  height: 30, borderRadius: 4, border: '1px solid var(--ui-stroke-secondary)', background: 'transparent',
  color: 'var(--ui-text-primary)', fontSize: 12, padding: '0 6px', minWidth: 0
}

function RequirementDialog({ wsId, data, editing, onClose }) {
  const { run, busy, error } = useRunner(wsId)
  const base = editing ? editing.requirement : null
  const [statement, setStatement] = useState(base ? base.statement : '')
  const [kind, setKind] = useState(base ? base.kind : 'document')
  const [form, setForm] = useState(rulesToForm(base ? base.rules : null))
  const [source, setSource] = useState(base && base.source ? base.source.code : '')
  const [excerpt, setExcerpt] = useState(base ? base.excerpt || '' : '')
  const submit = async () => {
    const body = { statement, kind, rules: formToRules(form, kind) }
    if (source && excerpt.trim()) Object.assign(body, { source, excerpt })
    try {
      if (editing) await run('/requirements/' + editing.code + '/revise', { ...body, expected_revision: data.workspace.revision })
      else await run('/requirements', body)
      onClose()
    } catch { /* error shown inline; inputs kept */ }
  }
  return h(DeskDialog, {
    open: true, onOpenChange: o => !o && onClose(), wide: true,
    title: editing ? `Correct ${editing.code} (creates v${base.version + 1})` : 'Add a requirement',
    description: editing ? 'The current version stays in the history. Reviews of the old version become stale.' : 'Entered by you, so it counts as confirmed. Quote the instructions if you can; it is kept as evidence.',
    footer: h('div', { style: S.row }, h(Button, { variant: 'outline', onClick: onClose }, 'Cancel'), h(Button, { onClick: submit, disabled: busy || !statement.trim() }, busy ? 'Saving…' : 'Save'))
  },
    h(Field, { label: 'Requirement in plain words' }, h(Textarea, { value: statement, onChange: e => setStatement(e.target.value), rows: 2, autoFocus: true })),
    h(Field, { label: 'Kind' }, h('select', { value: kind, onChange: e => setKind(e.target.value), style: nativeSelect }, KINDS.map(([k, label]) => h('option', { key: k, value: k }, label)))),
    h(RulesEditor, { kind, form, setForm }),
    h('div', { style: S.grid2 },
      h(Field, { label: 'Source (optional)' }, h('select', { value: source, onChange: e => setSource(e.target.value), style: nativeSelect },
        h('option', { value: '' }, 'No source'),
        (data.sources || []).map(s => h('option', { key: s.code, value: s.code }, `${s.code} · ${s.label}`)))),
      h(Field, { label: 'Exact quote from that source', hint: 'Must occur in the saved copy (spacing and quote marks may differ).' },
        h(Textarea, { value: excerpt, onChange: e => setExcerpt(e.target.value), rows: 2, disabled: !source }))),
    h(ErrorLine, { error }))
}

function MapDialog({ wsId, data, item, mapping, onClose }) {
  const { run, busy, error } = useRunner(wsId)
  const files = useQuery({ queryKey: keys.files(wsId), queryFn: () => api('/workspaces/' + encodeURIComponent(wsId) + '/files') })
  const [fieldCode, setFieldCode] = useState(mapping && mapping.field ? mapping.field.code : '')
  const [newLabel, setNewLabel] = useState('')
  const [docPath, setDocPath] = useState(mapping && mapping.document ? mapping.document.rel_path : '')
  const [filter, setFilter] = useState('')
  const fields = data.fields || []
  const list = ((files.data && files.data.files) || []).filter(f => !filter || f.rel_path.toLowerCase().includes(filter.toLowerCase()))
  const submit = async () => {
    try {
      // One request: a new field is created in the same transaction as the mapping.
      const newField = !fieldCode && newLabel.trim() ? { label: newLabel.trim(), kind: item.requirement.kind === 'text' ? 'text' : 'upload' } : null
      await run('/mappings', { requirement: item.code, field: fieldCode || null, new_field: newField, document: docPath || null, mapping: mapping ? mapping.code : null, expected_revision: data.workspace.revision })
      onClose()
    } catch { /* shown inline */ }
  }
  return h(DeskDialog, {
    open: true, onOpenChange: o => !o && onClose(), wide: true,
    title: mapping ? `Change ${mapping.code} for ${item.code}` : `Map ${item.code} to a portal field and a file`,
    description: 'Use the portal field label exactly as the portal shows it. Changing the field or the file makes earlier reviews of this mapping stale.',
    footer: h('div', { style: S.row }, h(Button, { variant: 'outline', onClick: onClose }, 'Cancel'), h(Button, { onClick: submit, disabled: busy || (!fieldCode && !newLabel.trim() && !docPath) }, busy ? 'Saving…' : 'Save mapping'))
  },
    h('div', { style: { ...S.small, ...S.muted } }, item.code + ': ' + item.requirement.statement),
    h('div', { style: S.grid2 },
      h(Field, { label: 'Portal field' }, h('select', { value: fieldCode, onChange: e => setFieldCode(e.target.value), style: nativeSelect },
        h('option', { value: '' }, fields.length ? 'New field…' : 'New field (type its label)'),
        fields.map(f => h('option', { key: f.code, value: f.code }, `${f.code} · ${f.label}`)))),
      fieldCode ? null : h(Field, { label: 'Exact label of the new field' }, h(Input, { value: newLabel, onChange: e => setNewLabel(e.target.value), placeholder: 'Research statement (PDF)' }))),
    h(Field, { label: 'File in the material folder', hint: docPath ? 'Selected: ' + docPath : 'Pick the exact file you will upload.' },
      h(Input, { value: filter, onChange: e => setFilter(e.target.value), placeholder: 'Filter files…', spellCheck: false })),
    h('div', { role: 'listbox', 'aria-label': 'Files', style: { maxHeight: 220, overflowY: 'auto', border: '1px solid var(--ui-stroke-tertiary)', borderRadius: 4 } },
      files.isLoading ? h('div', { style: { padding: 8, ...S.small, ...S.muted } }, 'Listing files…') :
        files.error ? h(ErrorLine, { error: files.error }) :
          list.length === 0 ? h('div', { style: { padding: 8, ...S.small, ...S.muted } }, 'No files match.') :
            list.map(f => h('button', {
              key: f.rel_path, type: 'button', role: 'option', 'aria-selected': docPath === f.rel_path, onClick: () => setDocPath(f.rel_path),
              style: { display: 'flex', width: '100%', justifyContent: 'space-between', gap: 8, padding: '5px 8px', border: 0, background: docPath === f.rel_path ? 'var(--ui-row-active-background)' : 'transparent', color: 'var(--ui-text-primary)', textAlign: 'left', cursor: 'pointer', fontSize: 12 }
            }, h('span', { style: { wordBreak: 'break-all' } }, f.rel_path), h('span', { style: { ...S.faint, whiteSpace: 'nowrap' } }, bytes(f.size))))),
    files.data && files.data.truncated ? h('div', { style: { ...S.tiny, ...S.muted } }, 'Only the first 2,000 files are listed.') : null,
    h(ErrorLine, { error }))
}

function ReviewDialog({ wsId, data, item, mapping, onClose }) {
  const { run, busy, error } = useRunner(wsId)
  const [note, setNote] = useState('')
  const doc = mapping.document
  const decide = async decision => {
    try {
      await run('/mappings/' + mapping.code + '/review', {
        decision, note, mapping_version: mapping.version, requirement_version: item.requirement.version, sha256: doc.sha256, expected_revision: data.workspace.revision
      })
      onClose()
    } catch { /* shown inline */ }
  }
  return h(DeskDialog, {
    open: true, onOpenChange: o => !o && onClose(),
    title: `Review ${mapping.code}: is this the right file for this field?`,
    description: 'Your review covers exactly this file revision, this field label and this version of the requirement. If any of them changes, the review becomes stale.',
    footer: h('div', { style: S.row },
      h(Button, { variant: 'outline', onClick: onClose }, 'Cancel'),
      h(Button, { variant: 'secondary', onClick: () => decide('reject'), disabled: busy }, 'No, wrong file'),
      h(Button, { onClick: () => decide('accept'), disabled: busy, autoFocus: true }, busy ? 'Saving…' : 'Yes, this revision is right'))
  },
    h('div', { style: { ...S.col, gap: 4, ...S.small } },
      h('div', null, h('span', { style: S.muted }, 'Portal field: '), '“' + (mapping.field ? mapping.field.label : '') + '”'),
      h('div', null, h('span', { style: S.muted }, 'File: '), h('span', { style: { wordBreak: 'break-all' } }, doc.rel_path), ' · ' + bytes(doc.size)),
      h('div', { style: S.row }, h('span', { style: S.muted }, 'Revision (SHA-256): '), h('span', { style: S.mono }, doc.sha256), h(CopyButton, { text: doc.sha256, label: 'Copy hash' })),
      h('div', null, h('span', { style: S.muted }, 'Requirement: '), `${item.code} v${item.requirement.version}: ${item.requirement.statement}`),
      h(Checks, { checks: mapping.checks })),
    h(Field, { label: 'Note (optional)' }, h(Input, { value: note, onChange: e => setNote(e.target.value) })),
    h(ErrorLine, { error }))
}

function AttestDialog({ wsId, data, subject, onClose }) {
  // subject: { code, version, title, text, check?, mapping?, sha256?, docLabel? }
  const { run, busy, error } = useRunner(wsId)
  const [statement, setStatement] = useState('')
  const submit = async () => {
    try {
      await run('/attest', { subject: subject.code, version: subject.version, statement, check: subject.check || null, mapping: subject.mapping || null, sha256: subject.sha256 || null, expected_revision: data.workspace.revision })
      onClose()
    } catch { /* shown inline */ }
  }
  return h(DeskDialog, {
    open: true, onOpenChange: o => !o && onClose(), title: subject.title,
    description: 'Saved as your attestation: what you state, when. The plugin does not verify it, and it is labelled that way in the checklist.' +
      (subject.sha256 ? ` It is tied to revision ${short(subject.sha256)}; if that file changes you will be asked again.` : ''),
    footer: h('div', { style: S.row }, h(Button, { variant: 'outline', onClick: onClose }, 'Cancel'), h(Button, { onClick: submit, disabled: busy || !statement.trim() }, busy ? 'Saving…' : 'Record my attestation'))
  },
    h('div', { style: { ...S.small, ...S.muted } }, subject.text),
    h(Field, { label: subject.check ? 'What you verified, and how' : 'What you did or confirm' },
      h(Textarea, { value: statement, onChange: e => setStatement(e.target.value), rows: 3, autoFocus: true, placeholder: subject.check ? 'Counted 812 words with the portal’s counter, excluding references.' : 'Signed by Prof. Example on 2 March; scanned.' })),
    h(ErrorLine, { error }))
}

function ResolveDialog({ wsId, data, conflict, onClose }) {
  const { run, busy, error } = useRunner(wsId)
  const [governing, setGoverning] = useState('')
  const [resolution, setResolution] = useState('')
  const submit = async () => {
    try {
      await run('/conflicts/' + conflict.code + '/resolve', { resolution, governing: governing || null, expected_revision: data.workspace.revision })
      onClose()
    } catch { /* shown inline */ }
  }
  return h(DeskDialog, {
    open: true, onOpenChange: o => !o && onClose(), wide: true, title: `Resolve ${conflict.code}`,
    description: 'Both quoted instructions stay in the history. Say which one governs and why (for example, an email from the office).',
    footer: h('div', { style: S.row }, h(Button, { variant: 'outline', onClick: onClose }, 'Cancel'), h(Button, { onClick: submit, disabled: busy || !resolution.trim() }, busy ? 'Saving…' : 'Resolve'))
  },
    h('div', { style: S.small }, conflict.description),
    conflict.excerpts.map((e, i) => h('div', { key: i, style: { ...S.quote, ...S.small } }, h('span', { style: S.muted }, e.source + ': '), e.excerpt)),
    h(Field, { label: 'Governing requirement' }, h('select', { value: governing, onChange: e => setGoverning(e.target.value), style: nativeSelect },
      h('option', { value: '' }, 'None in particular'), conflict.requirements.map(code => h('option', { key: code, value: code }, code)))),
    h(Field, { label: 'Resolution' }, h(Textarea, { value: resolution, onChange: e => setResolution(e.target.value), rows: 3 })),
    h(ErrorLine, { error }))
}

function SourceDialog({ wsId, onClose }) {
  const { run, busy, error } = useRunner(wsId)
  const files = useQuery({ queryKey: keys.files(wsId), queryFn: () => api('/workspaces/' + encodeURIComponent(wsId) + '/files') })
  const [mode, setMode] = useState('file')
  const [path, setPath] = useState('')
  const [label, setLabel] = useState('')
  const [text, setText] = useState('')
  const [origin, setOrigin] = useState('')
  const candidates = ((files.data && files.data.files) || []).filter(f => /\.(txt|md|html?|pdf)$/i.test(f.rel_path))
  const submit = async () => {
    try {
      await run('/sources', mode === 'file' ? { path, label: label || null, origin_note: origin || null } : { text, label: label || 'Pasted portal text', origin_note: origin || null }, { recheck: false })
      onClose()
    } catch { /* shown inline */ }
  }
  return h(DeskDialog, {
    open: true, onOpenChange: o => !o && onClose(), wide: true, title: 'Add instructions',
    description: 'A saved copy is kept with its capture date. It is evidence of what the instructions said then, not of what the live portal says now.',
    footer: h('div', { style: S.row }, h(Button, { variant: 'outline', onClick: onClose }, 'Cancel'), h(Button, { onClick: submit, disabled: busy || (mode === 'file' ? !path : !text.trim()) }, busy ? 'Saving…' : 'Save copy'))
  },
    h('div', { style: S.row, role: 'radiogroup', 'aria-label': 'Source type' },
      h(Button, { variant: mode === 'file' ? 'secondary' : 'ghost', size: 'sm', onClick: () => setMode('file'), 'aria-pressed': mode === 'file' }, 'File in the folder'),
      h(Button, { variant: mode === 'text' ? 'secondary' : 'ghost', size: 'sm', onClick: () => setMode('text'), 'aria-pressed': mode === 'text' }, 'Paste text')),
    mode === 'file'
      ? h(Field, { label: 'Instruction file (.txt, .md, .html, .pdf with a text layer)' },
          h('select', { value: path, onChange: e => setPath(e.target.value), style: nativeSelect },
            h('option', { value: '' }, files.isLoading ? 'Listing…' : candidates.length ? 'Choose a file' : 'No text, HTML or PDF files found'),
            candidates.map(f => h('option', { key: f.rel_path, value: f.rel_path }, f.rel_path))))
      : h(Field, { label: 'Text copied from the portal or guidance', hint: 'Paste only the relevant part. The agent quotes from this copy.' }, h(Textarea, { value: text, onChange: e => setText(e.target.value), rows: 8 })),
    h('div', { style: S.grid2 },
      h(Field, { label: 'Label (optional)' }, h(Input, { value: label, onChange: e => setLabel(e.target.value) })),
      h(Field, { label: 'Where it came from (optional)', hint: 'URL or description; never fetched.' }, h(Input, { value: origin, onChange: e => setOrigin(e.target.value) }))),
    h(ErrorLine, { error }))
}

function ExportDialog({ wsId, onClose }) {
  const [state, setState] = useState({ busy: true, out: null, error: null })
  useEffect(() => {
    let live = true
    api('/workspaces/' + encodeURIComponent(wsId) + '/export', { method: 'POST' })
      .then(out => live && setState({ busy: false, out, error: null }))
      .catch(error => live && setState({ busy: false, out: null, error }))
    return () => { live = false }
  }, [wsId])
  const out = state.out
  return h(DeskDialog, {
    open: true, onOpenChange: o => !o && onClose(), wide: true, title: 'Hand-back checklist',
    description: 'Written after a fresh recheck. Files were hashed again after writing; if any changed, the export says SNAPSHOT ONLY.',
    footer: h(Button, { onClick: onClose }, 'Close')
  },
    state.busy ? h('div', { style: { ...S.row, ...S.small } }, h(Loader, null), 'Rechecking and writing…') : null,
    h(ErrorLine, { error: state.error }),
    out ? h('div', { style: { ...S.col, gap: 6, ...S.small } },
      h('div', { style: { color: out.revalidated ? 'var(--ui-text-secondary)' : 'var(--ui-red)', fontWeight: 600 } }, out.note),
      h('div', { style: S.row }, h('span', { style: S.muted }, 'Checklist: '), h('span', { style: S.mono }, out.checklist), h(CopyButton, { text: out.checklist, label: 'Copy path' })),
      h('div', { style: S.row }, h('span', { style: S.muted }, 'Manifest: '), h('span', { style: S.mono }, out.manifest), h(CopyButton, { text: out.manifest, label: 'Copy path' })),
      h('pre', { style: { ...S.mono, whiteSpace: 'pre-wrap', maxHeight: 360, overflowY: 'auto', border: '1px solid var(--ui-stroke-tertiary)', borderRadius: 4, padding: 10, margin: 0 } }, out.checklist_text)) : null)
}

// ───────────────────────────────────────────── rows

const CHECK_NAME = { presence: 'File', format: 'Format', size: 'Size', pages: 'Pages', words: 'Words', encryption: 'Password' }
const CHECK_ICON = { pass: 'check', fail: 'close', unknown: 'question', not_applicable: 'dash' }
const CHECK_WORD = { pass: 'passed', fail: 'failed', unknown: 'not checked', not_applicable: 'n/a' }

function chipLabel(c) {
  const name = CHECK_NAME[c.check] || c.check
  const lim = c.limit || {}
  if (c.check === 'pages' && typeof c.measurement === 'number') return `${name} ${c.measurement}${lim.max != null ? ' of max ' + lim.max : lim.min != null ? ', min ' + lim.min : ''}`
  if (c.check === 'size' && typeof c.measurement === 'number') return `${name} ${bytes(c.measurement)}`
  if (c.check === 'words' && c.measurement && typeof c.measurement === 'object') {
    const vals = Object.values(c.measurement).filter(v => typeof v === 'number')
    if (vals.length) {
      const lo = Math.min(...vals), hi = Math.max(...vals)
      return `${name} ${lo === hi ? lo : lo + '–' + hi}${lim.max != null ? ' of max ' + lim.max : ''}${c.estimate ? ' (est.)' : ''}`
    }
  }
  if (c.check === 'format' && c.measurement && c.measurement.detected) return `${name} ${String(c.measurement.detected).toUpperCase()}`
  return name
}

function Checks({ checks }) {
  if (!checks || !checks.length) return null
  return h('div', { style: { ...S.row, gap: 4 } }, checks.map((c, i) => h(Badge, {
    key: i, variant: c.attested ? 'outline' : CHECK_VARIANT[c.status] || 'muted',
    title: `${c.check}: ${c.status}${c.estimate ? ' (estimate)' : ''}\n${c.detail || ''}\nMethod: ${c.method || '—'}${c.attested ? '\nVerified by you (attestation): ' + c.attested.statement : ''}`
  }, h(Codicon, { name: c.attested ? 'person' : CHECK_ICON[c.status] || 'circle', size: 11 }),
  chipLabel(c) + ' · ' + (c.attested ? 'verified by you' : CHECK_WORD[c.status] || c.status))))
}

function CheckDetails({ checks }) {
  return h('div', { style: { ...S.col, gap: 4 } }, (checks || []).map((c, i) => h('div', { key: i, style: { ...S.tiny, display: 'grid', gridTemplateColumns: '90px 1fr', gap: 6 } },
    h('span', { style: { ...S.muted } }, c.check + ' · ' + c.status + (c.estimate ? ' (est.)' : '')),
    h('span', null, c.detail, c.method ? h('span', { style: S.faint }, ' — ' + c.method) : null,
      c.attested ? h('div', { style: { color: 'var(--ui-text-secondary)' } }, 'Verified by you (attestation, not a machine check): “' + c.attested.statement + '” · ' + when(c.attested.at)) : null))))
}

function MappingBlock({ wsId, data, item, mapping, open, setDialog }) {
  const doc = mapping.document
  const field = mapping.field
  const review = mapping.review || {}
  const unknownChecks = (mapping.checks || []).filter(c => c.status === 'unknown' && !c.attested && c.check !== 'presence')
  const canReview = doc && doc.sha256 && field && item.requirement.status === 'confirmed'
  return h('div', { style: S.sub },
    h('div', { style: { ...S.row, justifyContent: 'space-between' } },
      h('div', { style: S.row },
        (item.mappings || []).length > 1 || mapping.state !== item.state ? h(StateBadge, { state: mapping.state }) : null,
        h('span', { style: { ...S.tiny, ...S.muted } }, 'Mapping ' + mapping.code)),
      review.state === 'current' && review.latest
        ? h('span', { style: { ...S.tiny, color: 'var(--ui-text-secondary)' } }, h(Codicon, { name: 'pass', size: 11 }), ` Reviewed by ${review.latest.actor_label} · ${when(review.latest.created_at)}`)
        : review.state === 'stale' ? h('span', { style: { ...S.tiny, color: 'var(--ui-yellow, #d7a400)' } }, h(Codicon, { name: 'history', size: 11 }), ' Earlier review is stale') : null),
    h('div', { style: S.kv },
      h('span', { style: S.key }, 'Portal field'),
      h('span', { style: { fontSize: 12.5, color: 'var(--ui-text-primary)', wordBreak: 'break-word' } }, field ? '“' + field.label + '”' : h('em', { style: S.muted }, 'not chosen')),
      h('span', { style: S.key }, 'File'),
      h('span', { style: { ...S.row, gap: 6, fontSize: 12.5 } },
        doc ? h('span', { style: { color: 'var(--ui-text-primary)', wordBreak: 'break-all' } }, doc.rel_path) : h('em', { style: S.muted }, 'no file'),
        doc && doc.sha256 ? h('span', { style: { ...S.mono, ...S.faint }, title: 'SHA-256 ' + doc.sha256 }, 'rev ' + short(doc.sha256)) : null,
        doc && doc.size != null ? h('span', { style: { ...S.tiny, ...S.faint } }, bytes(doc.size)) : null)),
    h(Checks, { checks: mapping.checks }),
    review.state === 'stale' ? h('div', { style: { ...S.tiny, color: 'var(--ui-text-secondary)' } }, 'Your earlier review no longer covers this: ' + review.stale_because.join('; ') + '.') : null,
    open ? h(CheckDetails, { checks: mapping.checks }) : null,
    h('div', { style: { ...S.row, gap: 6 } },
      canReview
        ? h(Button, { size: 'xs', variant: review.state === 'current' ? 'ghost' : 'default', onClick: () => setDialog({ type: 'review', item, mapping }) }, review.state === 'current' ? 'Review again' : 'Review this revision')
        : null,
      unknownChecks.map(c => h(Button, {
        key: c.check, size: 'xs', variant: 'outline',
        onClick: () => setDialog({ type: 'attest', subject: { code: item.code, version: item.requirement.version, check: c.check, mapping: mapping.code, sha256: doc && doc.sha256, title: `Verify the ${c.check} check yourself`, text: `Plugin result: unknown. ${c.detail}` } })
      }, 'Verify ' + c.check + ' by hand')),
      h(Button, { size: 'xs', variant: 'ghost', onClick: () => setDialog({ type: 'map', item, mapping }) }, 'Change field or file')))
}

function NextStep({ reasons }) {
  if (!reasons.length) return null
  const [first, ...rest] = reasons
  const prefix = r => (r.mapping ? r.mapping + ' · ' : '')
  return h('div', { style: { ...S.col, gap: 4 } },
    h('div', { style: S.next },
      h(Codicon, { name: 'arrow-right', size: 13, style: { marginTop: 2, color: 'var(--ui-accent)' } }),
      h('div', { style: { ...S.col, gap: 2 } },
        h('span', { style: { fontSize: 12.5, color: 'var(--ui-text-primary)' } }, h('strong', null, 'Next: '), first.next || first.message),
        first.next ? h('span', { style: { ...S.tiny, ...S.muted } }, prefix(first) + first.message) : null)),
    rest.length ? h('ul', { style: { margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 2 } },
      rest.map((r, i) => h('li', { key: i, style: { ...S.tiny, color: 'var(--ui-text-secondary)' } },
        prefix(r) + r.message, r.next ? h('span', { style: S.muted }, ' Next: ' + r.next) : null))) : null)
}

function RequirementRow({ wsId, data, item, setDialog }) {
  const [open, setOpen] = useState(false)
  const req = item.requirement
  const reasons = item.reasons || []
  const mappings = item.mappings || []
  const needsMapping = ['document', 'text'].includes(req.kind) && req.status === 'confirmed'
  const attestable = ['human_action', 'attestation', 'other'].includes(req.kind) && req.status === 'confirmed'
  const src = req.source
  return h('div', { style: { ...S.card, gap: 10, opacity: item.state === 'excluded' ? 0.6 : 1 } },
    h('div', { style: { ...S.row, alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'nowrap' } },
      h('div', { style: { ...S.col, gap: 5, flex: '1 1 auto' } },
        h('div', { style: { ...S.row, alignItems: 'flex-start', flexWrap: 'nowrap' } },
          h(StateBadge, { state: item.state }),
          h('span', { style: S.primaryText }, req.statement)),
        h('div', { style: { ...S.row, ...S.tiny, ...S.muted, gap: 6 } },
          h('span', { style: S.faint }, `${item.code} v${req.version} · ${req.kind.replace('_', ' ')}`),
          (req.rules_text || []).length ? h('span', null, '· ' + req.rules_text.join(' · ')) : null,
          item.deadline ? h('span', null, `· due ${when(item.deadline.due_utc)} (${timeLeft(item.deadline.remaining_hours)})`) : null)),
      h(Button, { size: 'xs', variant: 'ghost', 'aria-expanded': open, onClick: () => setOpen(!open) }, h(Codicon, { name: open ? 'chevron-up' : 'chevron-down', size: 12 }), open ? 'Less' : 'Details')),
    req.excerpt
      ? h('div', { style: { ...S.quote, ...S.small } }, '“' + req.excerpt + '”',
          h('div', { style: { ...S.tiny, ...S.muted, marginTop: 2 } }, src ? `${src.code} ${src.label} · saved copy from ${when(src.captured_at)}${req.locator && req.locator.page ? ' · p. ' + req.locator.page : ''} · not the live portal` : ''))
      : h('div', { style: { ...S.tiny, ...S.muted } }, 'No quoted source (entered by you).'),
    item.attested ? h('div', { style: { ...S.small, color: 'var(--ui-text-secondary)' } }, h(Codicon, { name: 'person', size: 11 }), ` Confirmed by you: “${item.attested.statement}” · ${when(item.attested.at)} (your attestation, not a machine check)`) : null,
    mappings.map(m => h(MappingBlock, { key: m.code, wsId, data, item, mapping: m, open, setDialog })),
    h(NextStep, { reasons }),
    open ? h('div', { style: { ...S.tiny, ...S.faint } }, `Proposed by ${req.created_by}${req.decided_by ? ' · confirmed by ' + req.decided_by + ' ' + when(req.decided_at) : ''}`) : null,
    h('div', { style: { ...S.row, gap: 6, justifyContent: 'space-between' } },
      h('div', { style: { ...S.row, gap: 6 } },
        needsMapping ? h(Button, { size: 'xs', variant: mappings.length ? 'ghost' : 'default', onClick: () => setDialog({ type: 'map', item }) }, mappings.length ? 'Add another field' : 'Map to field and file') : null,
        attestable && !item.attested ? h(Button, { size: 'xs', variant: 'default', onClick: () => setDialog({ type: 'attest', subject: { code: item.code, version: req.version, title: `Mark ${item.code} done`, text: req.statement } }) }, 'Mark done') : null),
      h('div', { style: { ...S.row, gap: 2 } },
        req.status === 'confirmed' ? h(Button, { size: 'xs', variant: 'ghost', onClick: () => setDialog({ type: 'requirement', editing: item }) }, 'Correct') : null,
        req.status === 'confirmed' && item.state !== 'excluded' ? h(Button, { size: 'xs', variant: 'ghost', onClick: () => setDialog({ type: 'decide', code: item.code, version: req.version, decision: 'withdraw', text: req.statement }) }, 'Not applicable') : null,
        item.state === 'excluded' && req.status === 'withdrawn' ? h(Button, { size: 'xs', variant: 'ghost', onClick: () => setDialog({ type: 'decide', code: item.code, version: req.version, decision: 'restore', text: req.statement }) }, 'Restore') : null)))
}

function ProposalCard({ wsId, data, proposal, item, action }) {
  const { run, busy, error } = useRunner(wsId)
  const target = item ? (item.pending || item.requirement) : null
  const decide = async decision => {
    try {
      if (proposal.kind === 'action') await run('/actions/' + proposal.code + '/decide', { version: proposal.version, decision, expected_revision: data.workspace.revision })
      else await run('/requirements/' + proposal.code + '/decide', { version: proposal.version, decision, expected_revision: data.workspace.revision })
    } catch { /* shown inline */ }
  }
  const src = target && target.source
  return h('div', { style: S.card },
    h('div', { style: S.row },
      h(Badge, { variant: 'default' }, h(Codicon, { name: 'sparkle', size: 11 }), 'Agent proposal'),
      h('span', { style: { ...S.small, ...S.faint } }, `${proposal.code} v${proposal.version}`),
      item && item.pending ? h('span', { style: { ...S.tiny, ...S.muted } }, `revises v${item.requirement.version}, which still applies`) : null),
    h('div', { style: { fontSize: 13, wordBreak: 'break-word' } }, proposal.statement),
    target && (target.rules_text || []).length ? h('div', { style: { ...S.tiny, ...S.muted } }, target.rules_text.join(' · ')) : null,
    target && target.excerpt ? h('div', { style: { ...S.quote, ...S.small } }, '“' + target.excerpt + '”',
      src ? h('div', { style: { ...S.tiny, ...S.faint, marginTop: 2 } }, `${src.code} ${src.label} · saved ${when(src.captured_at)}${target.locator && target.locator.page ? ' · p. ' + target.locator.page : ''}${target.locator && target.locator.match !== 'exact' ? ' · matched after normalising spacing/quotes' : ''}`) : null) : null,
    action ? h('div', { style: { ...S.tiny, ...S.muted } }, 'Something only you can do. Confirming adds it to your actions; you mark it done yourself.') : null,
    h('div', { style: S.row },
      h(Button, { size: 'sm', onClick: () => decide('confirm'), disabled: busy }, busy ? 'Saving…' : `Confirm v${proposal.version}`),
      h(Button, { size: 'sm', variant: 'outline', onClick: () => decide('reject'), disabled: busy }, 'Reject')),
    h(ErrorLine, { error }))
}

function ActionRow({ wsId, data, action, setDialog }) {
  const doc = action.document_id ? (data.documents || []).find(d => d.id === action.document_id) : null
  return h('div', { style: { ...S.card, flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap' } },
    h('div', { style: { ...S.col, gap: 4, flex: '1 1 300px' } },
      h('div', { style: S.row }, h(StateBadge, { state: action.state }), h('span', { style: { ...S.small, ...S.faint } }, `${action.code} v${action.version}`)),
      h('div', { style: { fontSize: 13, wordBreak: 'break-word' } }, action.description),
      doc ? h('div', { style: { ...S.tiny, ...S.muted } }, 'About ' + doc.rel_path + (doc.sha256 ? ' · rev ' + short(doc.sha256) : '')) : null,
      action.attested ? h('div', { style: { ...S.tiny, color: 'var(--ui-text-secondary)' } }, `Done (your attestation): “${action.attested.statement}” · ${when(action.attested.at)}`) : null,
      (action.reasons || []).map((r, i) => h('div', { key: i, style: { ...S.tiny, color: 'var(--ui-text-secondary)' } }, r.message + (r.next ? ' Next: ' + r.next : '')))),
    ['human_action', 'stale'].includes(action.state)
      ? h(Button, { size: 'sm', variant: 'secondary', onClick: () => setDialog({ type: 'attest', subject: { code: action.code, version: action.version, sha256: doc ? doc.sha256 : null, title: `Mark ${action.code} done`, text: action.description } }) }, 'Mark done')
      : null)
}

// ───────────────────────────────────────────── workspace view

function WorkspaceView({ wsId, workspaces, onSwitch, onNew }) {
  const qc = useQueryClient()
  const readiness = useReadiness(wsId)
  const [dialog, setDialog] = useState(null)
  const [checking, setChecking] = useState(false)
  const [checkError, setCheckError] = useState(null)
  const [confirmDelete, setConfirmDelete] = useState(null)
  const recheck = useCallback(async () => {
    setChecking(true)
    setCheckError(null)
    try {
      const fresh = await api('/workspaces/' + encodeURIComponent(wsId) + '/check', { method: 'POST' })
      qc.setQueryData(keys.ws(wsId), fresh)
    } catch (err) {
      setCheckError(err)
    } finally {
      setChecking(false)
    }
  }, [wsId, qc])
  useEffect(() => { recheck() }, [recheck]) // opening the page re-hashes the mapped files once

  const data = readiness.data
  if (readiness.isLoading && !data) return h('div', { style: { ...S.row, ...S.small, padding: 24 } }, h(Loader, null), 'Loading…')
  if (readiness.error && !data) return h('div', { style: S.page }, h(ErrorLine, { error: readiness.error }), h(Button, { onClick: () => readiness.refetch() }, 'Retry'))
  if (!data) return null
  const items = data.items || []
  const active = items.filter(i => i.state !== 'excluded' && !(i.requirement && i.requirement.status === 'proposed'))
  const excluded = items.filter(i => i.state === 'excluded')
  const actions = (data.actions || []).filter(a => !['excluded', 'awaiting_confirmation'].includes(a.state))
  const openConflicts = (data.conflicts || []).filter(c => c.status === 'open')
  const blocked = data.summary.verdict === 'blocked'

  const closeDialog = () => setDialog(null)
  const doDecide = async () => {
    const d = confirmDelete || dialog
    await api('/workspaces/' + encodeURIComponent(wsId) + '/requirements/' + d.code + '/decide', { method: 'POST', body: { version: d.version, decision: d.decision, expected_revision: data.workspace.revision, idempotency_key: newKey() } })
    await recheck()
  }
  const startDelete = async () => {
    try {
      await api('/workspaces/' + encodeURIComponent(wsId) + '/delete', { method: 'POST', body: {} })
    } catch (err) {
      if (err.code === 'confirmation_required') setConfirmDelete({ type: 'delete', token: err.details.confirm_token, message: err.message })
      else setCheckError(err)
    }
  }

  return h('div', { style: S.page },
    // Header: what application, where, how fresh.
    h('header', { style: { ...S.col, gap: 8 } },
      h('div', { style: { ...S.row, justifyContent: 'space-between' } },
        h('div', { style: { ...S.col, gap: 2, flex: '1 1 300px' } },
          h('h2', { style: { margin: 0, fontSize: 18, fontWeight: 600, wordBreak: 'break-word' } }, data.workspace.title),
          h('div', { style: { ...S.tiny, ...S.muted, wordBreak: 'break-all' } }, h(Codicon, { name: 'folder', size: 11 }), ' ', data.workspace.material_root)),
        h('div', { style: S.row },
          workspaces.length > 1 ? h('select', { 'aria-label': 'Application', value: wsId, onChange: e => onSwitch(e.target.value), style: nativeSelect }, workspaces.map(w => h('option', { key: w.id, value: w.id }, w.title))) : null,
          h(Button, { size: 'sm', variant: 'secondary', onClick: recheck, disabled: checking, title: 'Re-read and hash every mapped file and source now' }, h(Codicon, { name: checking ? 'loading' : 'refresh', size: 12, spinning: checking }), checking ? 'Checking…' : 'Recheck'),
          h(Button, { size: 'sm', variant: 'secondary', onClick: () => setDialog({ type: 'export' }) }, h(Codicon, { name: 'checklist', size: 12 }), 'Export checklist'))),
      h('div', { role: 'status', style: { ...S.tiny, color: data.fresh ? 'var(--ui-text-tertiary)' : 'var(--ui-yellow)' } },
        data.fresh ? `Current: files re-read and hashed at ${when(data.checked_at)}. Not monitored continuously; recheck after you change files.` : `Snapshot from ${when(data.checked_at)}: files have not been re-read since. Recheck to update.`),
      h(ErrorLine, { error: checkError })),

    // Headline: blockers first, never a bare green summary.
    h('div', { style: { ...S.card, borderColor: blocked ? 'var(--ui-stroke-secondary)' : 'var(--ui-stroke-tertiary)' } },
      h('div', { style: { ...S.row, fontSize: 14, fontWeight: 600 } },
        h(Codicon, { name: data.summary.verdict === 'no_local_blockers' ? 'pass' : data.summary.verdict === 'empty' ? 'info' : 'warning', size: 14 }),
        data.summary.headline),
      h('div', { style: { ...S.tiny, ...S.muted } }, data.responsibility)),

    // Agent proposals waiting for the applicant.
    (data.proposals || []).length ? h(Section, { title: 'Waiting for your decision', count: data.proposals.length },
      data.proposals.map(p => h(ProposalCard, {
        key: p.kind + p.code + p.version, wsId, data, proposal: p,
        item: p.kind === 'requirement' ? items.find(i => i.code === p.code) : null, action: p.kind === 'action'
      }))) : null,

    h(Section, {
      title: 'Requirements', count: active.length,
      actions: h('div', { style: S.row },
        h(Button, { size: 'xs', variant: 'ghost', onClick: () => setDialog({ type: 'source' }) }, h(Codicon, { name: 'book', size: 12 }), 'Add instructions'),
        h(Button, { size: 'xs', variant: 'ghost', onClick: () => setDialog({ type: 'requirement' }) }, h(Codicon, { name: 'add', size: 12 }), 'Add requirement'))
    },
      active.length ? active.map(item => h(RequirementRow, { key: item.code, wsId, data, item, setDialog })) :
        h('div', { style: { ...S.card, ...S.small, ...S.muted } },
          (data.sources || []).length
            ? 'No confirmed requirements yet. Ask the agent in chat to “read the instructions and propose requirements with exact quotes”, or add one by hand.'
            : 'Start by adding the instructions (a file in the folder or pasted portal text). Then ask the agent to propose requirements, or add them by hand.')),

    actions.length ? h(Section, { title: 'Your actions', count: actions.length }, actions.map(a => h(ActionRow, { key: a.code, wsId, data, action: a, setDialog }))) : null,

    openConflicts.length ? h(Section, { title: 'Conflicting instructions', count: openConflicts.length },
      openConflicts.map(c => h('div', { key: c.code, style: S.card },
        h('div', { style: S.row }, h(StateBadge, { state: 'conflicting' }), h('span', { style: { ...S.small, ...S.faint } }, c.code + ' · ' + c.requirements.join(', '))),
        h('div', { style: S.small }, c.description),
        c.excerpts.map((e, i) => h('div', { key: i, style: { ...S.quote, ...S.small } }, h('span', { style: S.muted }, e.source + ': '), '“' + e.excerpt + '”')),
        h('div', null, h(Button, { size: 'sm', variant: 'secondary', onClick: () => setDialog({ type: 'resolve', conflict: c }) }, 'Resolve…'))))) : null,

    (data.notes || []).length ? h(Section, { title: 'Notes' }, h('ul', { style: { margin: 0, paddingLeft: 18 } }, data.notes.map((n, i) => h('li', { key: i, style: { ...S.small, ...S.muted } }, n.message)))) : null,

    h(Section, { title: 'Instructions on file', count: (data.sources || []).length },
      (data.sources || []).length ? h('div', { style: S.col }, data.sources.map(s => h('div', { key: s.code, style: { ...S.small, ...S.row } },
        h('span', { style: S.faint }, s.code), h('span', { style: { wordBreak: 'break-word' } }, s.label),
        h('span', { style: { ...S.tiny, ...S.muted } }, `${s.kind === 'file' ? s.rel_path + ' · ' : ''}rev ${s.revision || '—'} · saved ${when(s.captured_at)} · ${(s.chars || 0).toLocaleString()} chars`)))) :
        h('div', { style: { ...S.small, ...S.muted } }, 'None yet.')),

    excluded.length ? h('details', null, h('summary', { style: { ...S.small, ...S.muted, cursor: 'pointer' } }, `Excluded (${excluded.length})`),
      h('div', { style: { ...S.col, marginTop: 8 } }, excluded.map(item => h(RequirementRow, { key: item.code, wsId, data, item, setDialog })))) : null,

    h('footer', { style: { ...S.row, justifyContent: 'space-between', borderTop: '1px solid var(--ui-stroke-tertiary)', paddingTop: 12 } },
      h('div', { style: { ...S.tiny, ...S.faint } }, `Workspace ${data.workspace.id.slice(0, 8)} · revision ${data.workspace.revision}`),
      h('div', { style: S.row },
        h(Button, { size: 'xs', variant: 'ghost', onClick: onNew }, 'New application'),
        h(Button, { size: 'xs', variant: 'ghost', onClick: startDelete }, 'Delete these records…'))),

    // Dialogs
    dialog && dialog.type === 'requirement' ? h(RequirementDialog, { wsId, data, editing: dialog.editing, onClose: closeDialog }) : null,
    dialog && dialog.type === 'map' ? h(MapDialog, { wsId, data, item: dialog.item, mapping: dialog.mapping, onClose: closeDialog }) : null,
    dialog && dialog.type === 'review' ? h(ReviewDialog, { wsId, data, item: dialog.item, mapping: dialog.mapping, onClose: closeDialog }) : null,
    dialog && dialog.type === 'attest' ? h(AttestDialog, { wsId, data, subject: dialog.subject, onClose: closeDialog }) : null,
    dialog && dialog.type === 'resolve' ? h(ResolveDialog, { wsId, data, conflict: dialog.conflict, onClose: closeDialog }) : null,
    dialog && dialog.type === 'source' ? h(SourceDialog, { wsId, onClose: closeDialog }) : null,
    dialog && dialog.type === 'export' ? h(ExportDialog, { wsId, onClose: closeDialog }) : null,
    dialog && dialog.type === 'decide' ? h(ConfirmDialog, {
      open: true, onClose: closeDialog, onConfirm: doDecide,
      title: dialog.decision === 'withdraw' ? `Mark ${dialog.code} not applicable?` : `Restore ${dialog.code}?`,
      description: (dialog.decision === 'withdraw' ? 'It stops counting toward readiness. A new version records the change; nothing is erased. ' : 'It counts toward readiness again. ') + '“' + dialog.text + '”',
      confirmLabel: dialog.decision === 'withdraw' ? 'Mark not applicable' : 'Restore'
    }) : null,
    confirmDelete ? h(ConfirmDialog, {
      open: true, destructive: true, onClose: () => setConfirmDelete(null), confirmLabel: 'Delete records',
      title: 'Delete this application’s records?', description: confirmDelete.message,
      onConfirm: async () => {
        await api('/workspaces/' + encodeURIComponent(wsId) + '/delete', { method: 'POST', body: { confirm_token: confirmDelete.token } })
        setConfirmDelete(null)
        storage && storage.remove('workspace')
        await qc.invalidateQueries({ queryKey: keys.list })
        onSwitch(null)
      }
    }) : null)
}

// ───────────────────────────────────────────── page

function DeskPage() {
  const list = useWorkspaces()
  const [selected, setSelected] = useState(() => (storage ? storage.get('workspace', null) : null))
  const [creating, setCreating] = useState(false)
  const workspaces = (list.data && list.data.workspaces) || []
  const current = useMemo(() => workspaces.find(w => w.id === selected) || workspaces[0] || null, [workspaces, selected])
  const choose = id => {
    setSelected(id)
    setCreating(false)
    if (storage) id ? storage.set('workspace', id) : storage.remove('workspace')
  }
  if (list.isLoading) return h('div', { style: { ...S.row, ...S.small, padding: 24 } }, h(Loader, null), 'Loading Submission Desk…')
  if (list.error) {
    return h('div', { style: S.page },
      h('h2', { style: { margin: 0, fontSize: 16 } }, 'Submission Desk backend is not reachable'),
      h(ErrorLine, { error: list.error }),
      h('div', { style: { ...S.small, ...S.muted } }, 'Enable the Python half with “hermes plugins enable submission-desk” on the machine running Hermes, then restart Hermes. In chat, /submission-desk works without this page.'),
      h('div', null, h(Button, { onClick: () => list.refetch() }, 'Retry')))
  }
  if (!current || creating) {
    return h('div', { style: S.page },
      h('h2', { style: { margin: 0, fontSize: 18, fontWeight: 600 } }, 'Submission Desk'),
      h(SetupPanel, { onCreated: choose }),
      creating && workspaces.length ? h('div', null, h(Button, { variant: 'ghost', size: 'sm', onClick: () => setCreating(false) }, 'Back')) : null)
  }
  return h(WorkspaceView, { key: current.id, wsId: current.id, workspaces, onSwitch: choose, onNew: () => setCreating(true) })
}

export default {
  id: ID,
  name: 'Submission Desk',
  register(ctx) {
    rest = ctx.rest
    storage = ctx.storage
    ctx.onDispose(() => { rest = null; storage = null })
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: PATH }, render: () => h(DeskPage, null) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PATH, label: 'Submission Desk', codicon: 'checklist' } },
      { id: 'open', area: PALETTE_AREA, data: { id: ID + '.open', label: 'Open Submission Desk', keywords: ['application', 'grant', 'scholarship', 'checklist', 'submission'], run: () => host.navigate(PATH) } }
    ])
  }
}
