const STORAGE_KEY = 'tenaishi_mobile_base_url'

function isPrivateIpv4(host) {
  const parts = host.split('.').map(Number)
  if (
    parts.length !== 4 ||
    parts.some(
      (part) => !Number.isInteger(part) || part < 0 || part > 255
    )
  ) {
    return false
  }
  return (
    parts[0] === 10 ||
    (parts[0] === 192 && parts[1] === 168) ||
    (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31)
  )
}

function normalizeBaseUrl(value) {
  const text = String(value || '').trim().replace(/\/$/, '')
  if (!text.startsWith('http://')) {
    throw new Error('连接地址必须使用厂内 HTTP 地址')
  }
  const matched = text.match(/^http:\/\/([^/:]+)(?::(\d{1,5}))?$/)
  if (!matched) {
    throw new Error('连接地址不能包含路径，请填写 IP 和端口')
  }
  if (!isPrivateIpv4(matched[1])) {
    throw new Error('连接地址必须使用厂内局域网 IP')
  }
  const port = Number(matched[2] || 80)
  if (port < 1 || port > 65535) {
    throw new Error('端口必须在 1 到 65535 之间')
  }
  return `http://${matched[1]}:${port}`
}

function isIpv4(host) {
  const parts = host.split('.').map(Number)
  return (
    parts.length === 4 &&
    parts.every(
      (part) => Number.isInteger(part) && part >= 0 && part <= 255
    )
  )
}

function normalizeReleaseBaseUrl(value) {
  const text = String(value || '').trim().replace(/\/+$/, '')
  const matched = text.match(/^https:\/\/([^/:]+)(?::443)?$/)
  if (!matched) {
    throw new Error('正式环境必须配置个人 HTTPS API 域名')
  }
  const host = matched[1].toLowerCase()
  if (!host.includes('.') || isIpv4(host)) {
    throw new Error('正式环境必须使用个人 API 域名，不能使用 IP')
  }
  return `https://${host}`
}

function canEditConnection(envVersion) {
  return envVersion !== 'release'
}

function baseUrlForEnvironment(envVersion, options = {}) {
  if (envVersion === 'release') {
    return normalizeReleaseBaseUrl(options.releaseBaseUrl)
  }
  return loadSavedBaseUrl(options.wxApi)
}

function parseConnectionPayload(rawValue) {
  let payload
  try {
    payload = JSON.parse(rawValue)
  } catch (error) {
    throw new Error('二维码不是杭州特耐时连接码')
  }
  if (payload.version !== 1) {
    throw new Error('连接二维码版本不支持，请在电脑后台重新生成')
  }
  return { version: 1, base_url: normalizeBaseUrl(payload.base_url) }
}

function loadSavedBaseUrl(wxApi = wx) {
  const value = wxApi.getStorageSync(STORAGE_KEY)
  return value ? normalizeBaseUrl(value) : ''
}

function saveBaseUrl(wxApi = wx, value) {
  const normalized = normalizeBaseUrl(value)
  wxApi.setStorageSync(STORAGE_KEY, normalized)
  return normalized
}

function scanCode(wxApi = wx) {
  return new Promise((resolve, reject) => {
    wxApi.scanCode({ scanType: ['qrCode'], success: resolve, fail: reject })
  })
}

async function scanBaseUrl(wxApi = wx) {
  const result = await scanCode(wxApi)
  const payload = parseConnectionPayload(result.result)
  return payload.base_url
}

module.exports = {
  STORAGE_KEY,
  normalizeBaseUrl,
  normalizeReleaseBaseUrl,
  canEditConnection,
  baseUrlForEnvironment,
  parseConnectionPayload,
  loadSavedBaseUrl,
  saveBaseUrl,
  scanBaseUrl
}
