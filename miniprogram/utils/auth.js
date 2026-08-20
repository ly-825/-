const SESSION_KEY = 'tns_auth_session'
const ACCOUNT_KEY = 'tns_auth_account'

function authorizationHeader(wxApi) {
  if (!wxApi || typeof wxApi.getStorageSync !== 'function') {
    return {}
  }
  const token = wxApi.getStorageSync(SESSION_KEY)
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function saveSession(wxApi, token) {
  wxApi.setStorageSync(SESSION_KEY, token)
}

function safeAccount(account = {}) {
  return {
    username: String(account.username || ''),
    display_name: String(account.display_name || ''),
    role: String(account.role || '')
  }
}

function saveAccount(wxApi, account) {
  wxApi.setStorageSync(ACCOUNT_KEY, safeAccount(account))
}

function loadAccount(wxApi) {
  const account = wxApi.getStorageSync(ACCOUNT_KEY)
  return account && typeof account === 'object' ? safeAccount(account) : null
}

function clearSession(wxApi) {
  wxApi.removeStorageSync(SESSION_KEY)
  wxApi.removeStorageSync(ACCOUNT_KEY)
}

function hasSession(wxApi) {
  return Boolean(wxApi.getStorageSync(SESSION_KEY))
}

function handleUnauthorized(wxApi) {
  clearSession(wxApi)
  wxApi.reLaunch({ url: '/pages/auth/login' })
}

function loginCode(wxApi) {
  return new Promise((resolve, reject) => {
    wxApi.login({
      success(result) {
        if (result.code) {
          resolve(result.code)
          return
        }
        reject(new Error('微信登录失败，请重试'))
      },
      fail() {
        reject(new Error('微信登录失败，请重试'))
      }
    })
  })
}

async function login(wxApi, request) {
  const wxCode = await loginCode(wxApi)
  const result = await request('/api/auth/wechat/login', {
    method: 'POST',
    data: { wx_code: wxCode }
  })
  saveSession(wxApi, result.token)
  saveAccount(wxApi, result.account)
  return result
}

async function activate(wxApi, request, username, activationCode) {
  const wxCode = await loginCode(wxApi)
  const result = await request('/api/auth/wechat/activate', {
    method: 'POST',
    data: {
      username,
      activation_code: activationCode,
      wx_code: wxCode
    }
  })
  saveSession(wxApi, result.token)
  saveAccount(wxApi, result.account)
  return result
}

function homeForRole(role) {
  return role === 'employee' ? '/pages/plan/home' : '/pages/account/home'
}

module.exports = {
  SESSION_KEY,
  ACCOUNT_KEY,
  activate,
  authorizationHeader,
  clearSession,
  handleUnauthorized,
  hasSession,
  homeForRole,
  loadAccount,
  login,
  saveAccount
}
