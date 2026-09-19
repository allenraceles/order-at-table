const apiBase = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const keyName = 'mesa-staff-key'

export function getStaffKey() {
  return sessionStorage.getItem(keyName) || ''
}

export function setStaffKey(value) {
  if (value) sessionStorage.setItem(keyName, value)
  else sessionStorage.removeItem(keyName)
}

export async function staffApi(path, init = {}) {
  const key = getStaffKey()
  if (!key) throw Object.assign(new Error('Enter the staff access key to view live orders.'), { status: 401 })
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-Staff-Key': key, ...init.headers },
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) throw Object.assign(new Error(body?.detail || `Request failed (${response.status})`), { status: response.status })
  return body
}

export function uploadStaffImage(file, kind) {
  return staffApi('/api/staff/uploads/images?kind=' + encodeURIComponent(kind), {
    method: 'POST',
    headers: { 'Content-Type': file.type },
    body: file,
  })
}

export function formatTime(value) {
  return new Intl.DateTimeFormat('en-PH', { hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Manila' }).format(new Date(value))
}

export function elapsedTime(value) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000))
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
}

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character])
}
