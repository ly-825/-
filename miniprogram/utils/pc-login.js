const PREFIX = 'tns-inventory-login:v1:'

function parseQrPayload(value) {
  const text = String(value || '')
  if (!text.startsWith(PREFIX)) {
    throw new Error('这不是杭州特耐时库存系统的登录二维码')
  }
  const token = text.slice(PREFIX.length)
  if (!/^[A-Za-z0-9_-]+$/.test(token)) {
    throw new Error('登录二维码格式不正确')
  }
  return token
}

function scanCode(wxApi) {
  return new Promise((resolve, reject) => {
    wxApi.scanCode({
      scanType: ['qrCode'],
      success: resolve,
      fail: () => reject(new Error('未能读取二维码'))
    })
  })
}

async function readPcLogin(request, requestToken) {
  return request('/api/auth/pc-login/scan', {
    method: 'POST',
    data: { request_token: requestToken }
  })
}

async function scanPcLogin(wxApi, request) {
  const result = await scanCode(wxApi)
  const requestToken = parseQrPayload(result.result)
  const summary = await readPcLogin(request, requestToken)
  return { requestToken, summary }
}

function decidePcLogin(request, requestToken, approved) {
  if (typeof approved !== 'boolean') {
    return Promise.reject(new Error('请选择确认或拒绝'))
  }
  return request('/api/auth/pc-login/decision', {
    method: 'POST',
    data: { request_token: requestToken, approved }
  })
}

module.exports = {
  decidePcLogin,
  parseQrPayload,
  readPcLogin,
  scanPcLogin
}
