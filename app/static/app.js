const PLANS = [
  ['KIRO_ENTERPRISE_PRO', 'Pro'],
  ['KIRO_ENTERPRISE_PRO_PLUS', 'Pro+'],
  ['KIRO_ENTERPRISE_PRO_MAX', 'Pro Max'],
  ['KIRO_ENTERPRISE_PRO_POWER', 'Power'],
]

const state = {
  users: [],
  subscriptions: [],
  loaded: { overview: false, users: false, subscriptions: false },
  updatedAt: { overview: null, users: null, subscriptions: null },
  reportLoaded: false,
  reportLoadedFor: null,
  reportLoading: null,
  reportLoadingFor: null,
  reportMonth: null,
  reportRequestId: 0,
  reportController: null,
}
const $ = (selector) => document.querySelector(selector)
const $$ = (selector) => [...document.querySelectorAll(selector)]

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;')
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  const isJson = (response.headers.get('content-type') || '').includes('json')
  const data = isJson ? await response.json() : null
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') showLogin()
    const detail = data?.error?.message || data?.detail?.error?.message || data?.detail
    throw new Error(typeof detail === 'string' ? detail : `请求失败 (${response.status})`)
  }
  return data
}

function showLogin() {
  if (state.reportLoaded) {
    window.location.reload()
    return
  }
  $('#app-view').classList.add('hidden')
  $('#login-view').classList.remove('hidden')
}

function showApp(username) {
  $('#login-view').classList.add('hidden')
  $('#app-view').classList.remove('hidden')
  $('#current-user').textContent = username
  loadOverview()
}

function showError(error) {
  const box = $('#global-error')
  box.textContent = error.message || String(error)
  box.classList.remove('hidden')
  setTimeout(() => box.classList.add('hidden'), 8000)
}

function setBusy(button, busy) {
  button.disabled = busy
  button.dataset.label ||= button.textContent
  button.textContent = busy ? '处理中…' : button.dataset.label
}

function markUpdated(view) {
  const now = new Date()
  state.updatedAt[view] = now
  const formatted = new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  }).format(now)
  $(`#${view}-updated-at`).textContent = `最后更新：${formatted}`
}

function invalidateView(view) {
  state.loaded[view] = false
}

function setMonthlyReportBusy(busy) {
  $('#monthly-report-month').disabled = busy
  $('#view-monthly-report').disabled = busy
  $('#latest-monthly-report').disabled = busy
}

function setMonthlyReportMode(month, actualPeriod = null) {
  const mode = $('#monthly-report-mode')
  if (month) {
    mode.textContent = `当前月份：${actualPeriod || month}`
  } else {
    mode.textContent = actualPeriod ? `当前：最新月份（${actualPeriod}）` : '当前：最新月份'
  }
}

async function loadMonthlyReport({ force = false, month = state.reportMonth } = {}) {
  const requestedMonth = month || null
  const requestKey = requestedMonth || 'latest'
  if (state.reportLoaded && state.reportLoadedFor === requestKey && !force) return
  if (state.reportLoading && state.reportLoadingFor === requestKey && !force) {
    return state.reportLoading
  }

  state.reportController?.abort()
  const controller = new AbortController()
  const requestId = state.reportRequestId + 1
  state.reportRequestId = requestId
  state.reportController = controller
  state.reportLoadingFor = requestKey
  setMonthlyReportBusy(true)
  setMonthlyReportMode(requestedMonth)

  const loading = (async () => {
    const query = requestedMonth ? `?month=${encodeURIComponent(requestedMonth)}` : ''
    const response = await fetch(`/api/reports/monthly/view${query}`, {
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
    })
    if (!response.ok) {
      if (response.status === 401) showLogin()
      throw new Error(`月度报告加载失败 (${response.status})`)
    }
    const html = await response.text()
    if (requestId !== state.reportRequestId) return
    const host = $('#monthly-report-host')
    host.innerHTML = html
    if (typeof window.initMonthlyReport === 'function') window.initMonthlyReport()
    const actualPeriod = host.querySelector('.monthly-report')?.dataset.period || requestedMonth
    if (actualPeriod) $('#monthly-report-month').value = actualPeriod
    state.reportLoaded = true
    state.reportLoadedFor = requestKey
    setMonthlyReportMode(requestedMonth, actualPeriod)
  })()
  state.reportLoading = loading
  try {
    await loading
  } catch (error) {
    if (error.name !== 'AbortError') throw error
  } finally {
    if (requestId === state.reportRequestId) {
      state.reportLoading = null
      state.reportLoadingFor = null
      state.reportController = null
      setMonthlyReportBusy(false)
    }
  }
}

async function loadOverview({ force = false } = {}) {
  if (state.loaded.overview && !force) return
  const button = $('#refresh-overview')
  setBusy(button, true)
  try {
    const [data] = await Promise.all([api('/api/overview'), loadMonthlyReport({ force })])
    $('#stat-users').textContent = data.users
    $('#stat-subscriptions').textContent = data.subscriptions
    $('#stat-active').textContent = data.active
    $('#stat-pending').textContent = data.pending
    state.loaded.overview = true
    markUpdated('overview')
  } catch (error) { showError(error) } finally { setBusy(button, false) }
}

async function loadUsers({ force = false } = {}) {
  if (state.loaded.users && !force) return
  const button = $('#refresh-users')
  setBusy(button, true)
  try {
    const query = $('#user-search').value.trim()
    const data = await api(`/api/users${query ? `?q=${encodeURIComponent(query)}` : ''}`)
    state.users = data.items
    $('#users-count').textContent = `${data.total} 个用户`
    $('#users-body').innerHTML = data.items.length ? data.items.map((user) => `
      <tr>
        <td>${escapeHtml(user.user_name)}</td>
        <td>${escapeHtml(user.display_name || '-')}</td>
        <td>${escapeHtml(user.email || '-')}</td>
        <td><div class="user-actions">
          <button data-verify-email="${escapeHtml(user.user_id)}" data-user-name="${escapeHtml(user.user_name)}" ${user.email ? '' : 'disabled title="用户没有邮箱"'}>验证邮箱</button>
          <button data-reset-password="${escapeHtml(user.user_id)}" data-user-name="${escapeHtml(user.user_name)}" ${user.email ? '' : 'disabled title="用户没有邮箱"'}>重置密码</button>
          <button class="danger" data-delete-user="${escapeHtml(user.user_id)}" data-user-name="${escapeHtml(user.user_name)}">删除</button>
        </div></td>
      </tr>`).join('') : '<tr><td colspan="4">没有匹配用户</td></tr>'
    state.loaded.users = true
    markUpdated('users')
  } catch (error) { showError(error) } finally { setBusy(button, false) }
}

function csvCell(value) {
  const text = String(value ?? '')
  const protectedText = /^\s*[=+\-@]/.test(text) ? `'${text}` : text
  return `"${protectedText.replaceAll('"', '""')}"`
}

function buildUsersCsv(users) {
  const rows = [
    ['用户名称', '显示名称', '邮箱'],
    ...users.map((user) => [user.user_name, user.display_name || '', user.email || '']),
  ]
  return `\ufeff${rows.map((row) => row.map(csvCell).join(',')).join('\r\n')}\r\n`
}

async function exportUsersCsv() {
  const button = $('#export-users-button')
  setBusy(button, true)
  try {
    const data = await api('/api/users')
    const blob = new Blob([buildUsersCsv(data.items)], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `kiro-users-${new Date().toISOString().slice(0, 10)}.csv`
    link.hidden = true
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch (error) { showError(error) } finally { setBusy(button, false) }
}

async function loadSubscriptions({ force = false } = {}) {
  if (state.loaded.subscriptions && !force) return
  const button = $('#refresh-subscriptions')
  setBusy(button, true)
  try {
    const data = await api('/api/subscriptions')
    state.subscriptions = data.items
    $('#subscriptions-count').textContent = `${data.total} 条订阅`
    $('#subscriptions-body').innerHTML = data.items.length ? data.items.map((item) => `
      <tr>
        <td>${escapeHtml(item.user_name || item.principal_id)}</td>
        <td>${escapeHtml(item.email || '-')}</td>
        <td>${escapeHtml(planLabel(item.subscription_type))}</td>
        <td><span class="status ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span></td>
        <td>${escapeHtml(item.aggregated_type || '-')}</td>
        <td>
          <button data-change-subscription="${escapeHtml(item.principal_id)}" data-current-plan="${escapeHtml(item.subscription_type)}" ${item.status === 'active' ? '' : 'disabled title="仅 active 状态可以变更套餐"'}>变更</button>
          <button class="danger" data-cancel-subscription="${escapeHtml(item.principal_id)}">取消</button>
        </td>
      </tr>`).join('') : '<tr><td colspan="6">暂无订阅</td></tr>'
    state.loaded.subscriptions = true
    markUpdated('subscriptions')
  } catch (error) { showError(error) } finally { setBusy(button, false) }
}

function planLabel(value) {
  return PLANS.find(([key]) => key === value)?.[1] || value || '-'
}

function activateView(name) {
  $$('.view').forEach((view) => view.classList.add('hidden'))
  $(`#view-${name}`).classList.remove('hidden')
  $$('.nav-button').forEach((button) => button.classList.toggle('active', button.dataset.view === name))
  if (name === 'overview') loadOverview()
  if (name === 'users') loadUsers()
  if (name === 'subscriptions') loadSubscriptions()
}

function parseDelimitedRows(text) {
  const firstLine = text.split(/\r?\n/).find((line) => line.trim()) || ''
  const delimiter = firstLine.includes('\t') ? '\t' : ','
  const rows = []
  let row = []
  let field = ''
  let quoted = false
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index]
    if (quoted) {
      if (character === '"' && text[index + 1] === '"') {
        field += '"'
        index += 1
      } else if (character === '"') quoted = false
      else field += character
    } else if (character === '"') quoted = true
    else if (character === delimiter) {
      row.push(field)
      field = ''
    } else if (character === '\n') {
      row.push(field)
      rows.push(row)
      row = []
      field = ''
    } else if (character !== '\r') field += character
  }
  if (quoted) throw new Error('CSV 中存在未闭合的双引号')
  row.push(field)
  rows.push(row)
  return rows.filter((values) => values.some((value) => value.trim()))
}

function parseBatchUsers(text) {
  const rows = parseDelimitedRows(text.trim())
  if (!rows.length) throw new Error('请粘贴或选择 CSV 文件')
  const normalizeHeader = (value) => value.replace(/^\ufeff/, '').trim().toLowerCase().replaceAll(' ', '')
  const header = rows[0].map(normalizeHeader)
  const aliases = {
    user_name: ['用户名称', '用户名', 'user_name', 'username'],
    display_name: ['显示名称', 'display_name', 'displayname'],
    email: ['邮箱', '电子邮箱', 'email'],
  }
  const positions = Object.fromEntries(Object.entries(aliases).map(([key, values]) => [
    key, header.findIndex((value) => values.includes(value)),
  ]))
  const hasHeader = Object.values(positions).every((position) => position >= 0)
  const indexes = hasHeader ? positions : { user_name: 0, display_name: 1, email: 2 }
  const dataRows = rows.slice(hasHeader ? 1 : 0)
  if (!dataRows.length) throw new Error('CSV 中没有用户数据')
  if (dataRows.length > 100) throw new Error('一次最多导入 100 位用户')
  const users = []
  const errors = []
  const names = new Set()
  const emails = new Set()
  dataRows.forEach((values, rowIndex) => {
    const line = rowIndex + (hasHeader ? 2 : 1)
    const user = {
      user_name: (values[indexes.user_name] || '').trim(),
      display_name: (values[indexes.display_name] || '').trim(),
      email: (values[indexes.email] || '').trim(),
    }
    if (!user.user_name || !user.display_name || !user.email) errors.push(`第 ${line} 行缺少必填字段`)
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(user.email)) errors.push(`第 ${line} 行邮箱格式无效`)
    const normalizedName = user.user_name.toLowerCase()
    const normalizedEmail = user.email.toLowerCase()
    if (names.has(normalizedName)) errors.push(`第 ${line} 行用户名称重复`)
    if (emails.has(normalizedEmail)) errors.push(`第 ${line} 行邮箱重复`)
    names.add(normalizedName)
    emails.add(normalizedEmail)
    users.push(user)
  })
  if (errors.length) throw new Error(`${errors.slice(0, 5).join('；')}${errors.length > 5 ? `；另有 ${errors.length - 5} 项错误` : ''}`)
  return users
}

function updateBatchUsersPreview() {
  const preview = $('#batch-users-preview')
  try {
    const users = parseBatchUsers($('#batch-users-input').value)
    preview.textContent = `已读取 ${users.length} 位用户，字段校验通过`
    preview.classList.remove('error')
  } catch (error) {
    preview.textContent = error.message
    preview.classList.add('error')
  }
}

function openBatchUsersDialog() {
  $('#batch-users-form').reset()
  $('#batch-users-preview').textContent = '尚未读取用户'
  $('#batch-users-preview').classList.remove('error')
  $('#batch-users-result').textContent = ''
  $('#batch-users-result').classList.add('hidden')
  $('#batch-users-dialog').showModal()
}

function selectedAssignInputs() {
  return $$('#assign-user-list input[type="checkbox"]:checked')
}

function updateAssignSelectedCount() {
  $('#assign-selected-count').textContent = `已选择 ${selectedAssignInputs().length} 位`
}

function selectedAssignPlanLabel() {
  const selectedOption = $('#assign-plan option:checked')
  return selectedOption?.textContent?.trim() || $('#assign-plan').value
}

function filterAssignableUsers() {
  const query = $('#assign-user-search').value.trim().toLowerCase()
  $$('#assign-user-list .assign-user-option').forEach((option) => {
    option.classList.toggle('hidden', Boolean(query) && !option.dataset.search.includes(query))
  })
}

async function openAssignDialog() {
  try {
    const [usersData, subscriptionsData] = await Promise.all([
      api('/api/users'), api('/api/subscriptions'),
    ])
    state.users = usersData.items
    state.subscriptions = subscriptionsData.items
    const subscriptionsByUser = new Map(state.subscriptions.map((item) => [item.principal_id, item]))
    $('#assign-user-list').innerHTML = state.users.map((user) => {
      const subscription = subscriptionsByUser.get(user.user_id)
      const blocked = subscription && ['active', 'pending'].includes(subscription.status)
      const status = subscription ? `<span class="status ${escapeHtml(subscription.status)}">${escapeHtml(subscription.status)}</span>` : ''
      const search = `${user.user_name || ''} ${user.display_name || ''} ${user.email || ''}`.toLowerCase()
      return `<label class="assign-user-option ${blocked ? 'unavailable' : ''}" data-search="${escapeHtml(search)}">
        <input type="checkbox" value="${escapeHtml(user.user_id)}" ${blocked ? 'disabled' : ''}>
        <span><strong>${escapeHtml(user.user_name)}</strong><small>${escapeHtml(user.display_name || '-')} · ${escapeHtml(user.email || '无邮箱')}</small></span>${status}
      </label>`
    }).join('') || '<p class="empty-list">没有可显示的用户</p>'
    $('#assign-user-search').value = ''
    $('#assign-plan').innerHTML = PLANS.map(([value, label]) => `<option value="${value}">${label}</option>`).join('')
    $('#assign-error').textContent = ''
    $('#assign-result').textContent = ''
    $('#assign-result').classList.add('hidden')
    updateAssignSelectedCount()
    $('#assign-dialog').showModal()
  } catch (error) { showError(error) }
}

async function changeSubscription(principalId, currentPlan) {
  const choices = PLANS.map(([value, label], index) => `${index + 1}. ${label} (${value})`).join('\n')
  const answer = window.prompt(`选择新套餐编号：\n${choices}`, String(Math.max(1, PLANS.findIndex(([value]) => value === currentPlan) + 1)))
  if (!answer) return
  const selected = PLANS[Number(answer) - 1]
  if (!selected) return showError(new Error('套餐编号无效'))
  if (selected[0] === currentPlan) return showError(new Error(`当前已是 ${selected[1]} 套餐，无需变更`))
  try {
    await api(`/api/subscriptions/${encodeURIComponent(principalId)}`, {
      method: 'PATCH', body: JSON.stringify({ subscription_type: selected[0] }),
    })
    invalidateView('overview')
    await loadSubscriptions({ force: true })
  } catch (error) { showError(error) }
}

document.addEventListener('click', async (event) => {
  const button = event.target.closest('button')
  if (!button) return
  if (button.matches('.nav-button')) activateView(button.dataset.view)
  if (button.dataset.closeDialog !== undefined) $('#assign-dialog').close()
  if (button.dataset.verifyEmail) {
    if (!confirm(`确认向 ${button.dataset.userName} 发送邮箱验证邮件？`)) return
    setBusy(button, true)
    try {
      const data = await api(`/api/users/${encodeURIComponent(button.dataset.verifyEmail)}/verify-email`, { method: 'POST' })
      alert(data.message)
    } catch (error) { showError(error) } finally { setBusy(button, false) }
  }
  if (button.dataset.resetPassword) {
    if (!confirm(`确认向 ${button.dataset.userName} 发送密码重置邮件？`)) return
    setBusy(button, true)
    try {
      const data = await api(`/api/users/${encodeURIComponent(button.dataset.resetPassword)}/reset-password`, { method: 'POST' })
      alert(data.message)
    } catch (error) { showError(error) } finally { setBusy(button, false) }
  }
  if (button.dataset.deleteUser) {
    const userName = button.dataset.userName
    const confirmation = prompt(`删除 Identity Center 用户不可撤销。请输入完整用户名确认：\n${userName}`)
    if (confirmation === null) return
    if (confirmation !== userName) return showError(new Error('确认用户名不匹配，未删除用户'))
    setBusy(button, true)
    try {
      const data = await api(`/api/users/${encodeURIComponent(button.dataset.deleteUser)}?confirm_user_name=${encodeURIComponent(confirmation)}`, { method: 'DELETE' })
      alert(data.message)
      invalidateView('overview')
      invalidateView('subscriptions')
      await loadUsers({ force: true })
    } catch (error) { showError(error) } finally { setBusy(button, false) }
  }
  if (button.dataset.changeSubscription) changeSubscription(button.dataset.changeSubscription, button.dataset.currentPlan)
  if (button.dataset.cancelSubscription) {
    if (!confirm('确认取消该用户订阅？此操作会立即调用 AWS。')) return
    setBusy(button, true)
    try {
      await api(`/api/subscriptions/${encodeURIComponent(button.dataset.cancelSubscription)}`, { method: 'DELETE' })
      invalidateView('overview')
      await loadSubscriptions({ force: true })
    } catch (error) { showError(error) } finally { setBusy(button, false) }
  }
})

$('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault()
  const button = event.submitter
  const formElement = event.currentTarget
  const form = new FormData(formElement)
  setBusy(button, true)
  $('#login-error').textContent = ''
  try {
    const data = await api('/api/login', {
      method: 'POST', body: JSON.stringify({ username: form.get('username'), password: form.get('password') }),
    })
    formElement.reset()
    showApp(data.username)
  } catch (error) { $('#login-error').textContent = error.message } finally { setBusy(button, false) }
})

$('#logout-button').addEventListener('click', async () => {
  try { await api('/api/logout', { method: 'POST' }) } finally { showLogin() }
})
$('#refresh-overview').addEventListener('click', () => loadOverview({ force: true }))
$('#view-monthly-report').addEventListener('click', async () => {
  const month = $('#monthly-report-month').value
  if (!month) return showError(new Error('请选择报告月份'))
  state.reportMonth = month
  try { await loadMonthlyReport({ force: true, month }) }
  catch (error) { showError(error) }
})
$('#latest-monthly-report').addEventListener('click', async () => {
  state.reportMonth = null
  try { await loadMonthlyReport({ force: true, month: null }) }
  catch (error) { showError(error) }
})
$('#refresh-users').addEventListener('click', () => loadUsers({ force: true }))
$('#refresh-subscriptions').addEventListener('click', () => loadSubscriptions({ force: true }))
$('#search-users').addEventListener('click', () => loadUsers({ force: true }))
$('#user-search').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') loadUsers({ force: true })
})
$('#batch-users-button').addEventListener('click', openBatchUsersDialog)
$('#export-users-button').addEventListener('click', exportUsersCsv)
$('#close-batch-users').addEventListener('click', () => $('#batch-users-dialog').close())
$('#batch-users-input').addEventListener('input', updateBatchUsersPreview)
$('#batch-users-file').addEventListener('change', async (event) => {
  const file = event.target.files[0]
  if (!file) return
  if (file.size > 1024 * 1024) return showError(new Error('CSV 文件不能超过 1 MB'))
  $('#batch-users-input').value = await file.text()
  updateBatchUsersPreview()
})
$('#download-users-template').addEventListener('click', () => {
  const blob = new Blob(['\ufeff用户名称,显示名称,邮箱\n'], { type: 'text/csv;charset=utf-8' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = 'kiro-users-template.csv'
  link.click()
  URL.revokeObjectURL(link.href)
})
$('#batch-users-form').addEventListener('submit', async (event) => {
  event.preventDefault()
  let users
  try { users = parseBatchUsers($('#batch-users-input').value) }
  catch (error) { return showError(error) }
  if (!confirm(`确认导入 ${users.length} 位用户？新建成功后将自动发送验证邮件和密码重置邮件；已存在且用户名/邮箱匹配的用户会自动跳过；成功项不会因其他行失败而回滚。`)) return
  const button = event.submitter
  setBusy(button, true)
  try {
    const data = await api('/api/users/batch', { method: 'POST', body: JSON.stringify({ users }) })
    const labels = { created: '创建', skipped: '跳过', ready: '可创建', failed: '失败' }
    const lines = data.items.map((item) => `${labels[item.status] || item.status} | ${item.user_name} | ${item.message}`)
    $('#batch-users-result').textContent = `共 ${data.total} 位：创建 ${data.created}，跳过 ${data.skipped}，失败 ${data.failed}，验证邮件失败 ${data.verification_failed || 0}，密码重置邮件失败 ${data.password_reset_failed || 0}\n${lines.join('\n')}`
    $('#batch-users-result').classList.remove('hidden')
    if (data.created) {
      invalidateView('overview')
      await loadUsers({ force: true })
    }
  } catch (error) { showError(error) } finally { setBusy(button, false) }
})
$('#assign-button').addEventListener('click', openAssignDialog)
$('#assign-user-search').addEventListener('input', filterAssignableUsers)
$('#assign-user-list').addEventListener('change', (event) => {
  if (selectedAssignInputs().length > 100) {
    event.target.checked = false
    showError(new Error('一次最多选择 100 位用户'))
  }
  updateAssignSelectedCount()
})
$('#select-all-assignable').addEventListener('click', () => {
  const available = $$('#assign-user-list .assign-user-option:not(.hidden) input:not(:disabled):not(:checked)')
  const remaining = Math.max(0, 100 - selectedAssignInputs().length)
  available.slice(0, remaining).forEach((input) => { input.checked = true })
  updateAssignSelectedCount()
})
$('#clear-assigned-selection').addEventListener('click', () => {
  selectedAssignInputs().forEach((input) => { input.checked = false })
  updateAssignSelectedCount()
})
$('#assign-form').addEventListener('submit', async (event) => {
  event.preventDefault()
  const principalIds = selectedAssignInputs().map((input) => input.value)
  if (!principalIds.length) {
    $('#assign-error').textContent = '请至少选择一位用户'
    return
  }
  if (!confirm(`确认向 ${principalIds.length} 位用户分配 ${selectedAssignPlanLabel()}？成功项不会因其他用户失败而回滚。`)) return
  const button = event.submitter
  setBusy(button, true)
  $('#assign-error').textContent = ''
  try {
    const data = await api('/api/subscriptions/batch', {
      method: 'POST',
      body: JSON.stringify({ principal_ids: principalIds, subscription_type: $('#assign-plan').value }),
    })
    const usersById = new Map(state.users.map((user) => [user.user_id, user.user_name]))
    const lines = data.items.map((item) => `${item.success ? '成功' : '失败'} | ${usersById.get(item.principal_id) || item.principal_id} | ${item.message}`)
    $('#assign-result').textContent = `共 ${data.total} 位：成功 ${data.succeeded}，失败 ${data.failed}\n${lines.join('\n')}`
    $('#assign-result').classList.remove('hidden')
    if (data.succeeded) {
      invalidateView('overview')
      await loadSubscriptions({ force: true })
    }
  } catch (error) { $('#assign-error').textContent = error.message } finally { setBusy(button, false) }
})

api('/api/session').then((data) => showApp(data.username)).catch(showLogin)
