const app = getApp()
const auth = require('./auth')

class ConnectionError extends Error {
  constructor(message, code = 'CONNECTION_FAILED') {
    super(message)
    this.name = 'ConnectionError'
    this.code = code
  }
}

function configureBaseUrl(value) {
  app.globalData.baseUrl = String(value || '').replace(/\/$/, '')
}

function baseUrl() {
  const value = app.globalData.baseUrl
  if (!value) {
    throw new ConnectionError('尚未连接厂内库存系统', 'NOT_CONFIGURED')
  }
  return value.replace(/\/$/, '')
}

function trackedWriteData(data = {}) {
  if (!data.client_request_id) {
    throw new Error('库存写入缺少重试编号，请返回页面重新核对')
  }
  return data
}

function errorMessage(data, fallback) {
  if (!data) {
    return fallback
  }
  if (typeof data === 'string') {
    try {
      return errorMessage(JSON.parse(data), fallback)
    } catch (error) {
      return data || fallback
    }
  }
  if (Array.isArray(data.detail)) {
    return data.detail.map((item) => item.msg || item.message || JSON.stringify(item)).join('；')
  }
  if (data.detail) {
    return data.detail
  }
  if (data.message) {
    return data.message
  }
  return fallback
}

function request(path, options = {}) {
  let url
  try {
    url = `${baseUrl()}${path}`
  } catch (error) {
    return Promise.reject(error)
  }
  return new Promise((resolve, reject) => {
    wx.request({
      url,
      method: options.method || 'GET',
      data: options.data || {},
      timeout: 8000,
      header: {
        'content-type': 'application/json',
        ...auth.authorizationHeader(wx)
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
          return
        }
        if (res.statusCode === 401) {
          auth.handleUnauthorized(wx)
        }
        reject(new Error(errorMessage(res.data, '请求失败')))
      },
      fail() {
        reject(
          new ConnectionError(
            '无法连接后台，请确认手机和电脑连接同一工厂 Wi-Fi'
          )
        )
      }
    })
  })
}

function uploadFile(path, filePath, name = 'file') {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${baseUrl()}${path}`,
      filePath,
      name,
      timeout: 8000,
      header: auth.authorizationHeader(wx),
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          try {
            resolve(JSON.parse(res.data))
          } catch (error) {
            resolve(res.data)
          }
          return
        }
        if (res.statusCode === 401) {
          auth.handleUnauthorized(wx)
        }
        reject(new Error(errorMessage(res.data, '上传失败')))
      },
      fail() {
        reject(
          new ConnectionError(
            '无法连接后台，请确认手机和电脑连接同一工厂 Wi-Fi'
          )
        )
      }
    })
  })
}

module.exports = {
  configureBaseUrl,
  ConnectionError,
  request,
  uploadFile,
  health: () => request('/health'),
  summary: () => request('/api/mobile/summary'),
  productOptions: () => request('/api/mobile/product-options'),
  products: (params = {}) => request('/api/mobile/products', { data: params }),
  productBatches: (productCode) => request(`/api/mobile/products/${encodeURIComponent(productCode)}/batches`),
  productInbound: (data) => request('/api/mobile/products/inbound', { method: 'POST', data: trackedWriteData(data) }),
  productOutbound: (data) => request('/api/mobile/products/outbound', { method: 'POST', data: trackedWriteData(data) }),
  productTransactions: (params = {}) => request('/api/mobile/products/transactions', { data: params }),
  reverseProductTransaction: (id, data = {}) => request(`/api/mobile/products/transactions/${id}/reverse`, { method: 'POST', data: trackedWriteData(data) }),
  pendingScraps: () => request('/api/mobile/scraps/pending'),
  confirmScrap: (id, data) => request(`/api/mobile/scraps/${id}/confirm`, { method: 'POST', data: trackedWriteData(data) }),
  scraps: (params = {}) => request('/api/mobile/scraps', { data: params }),
  scrapBatches: (groupKey) => request('/api/mobile/scraps/batches', { data: { group_key: groupKey } }),
  scrapOutbound: (data) => request('/api/mobile/scraps/outbound', { method: 'POST', data: trackedWriteData(data) }),
  scrapTransactions: () => request('/api/mobile/scraps/transactions'),
  reverseScrapTransaction: (id, data = {}) => request(`/api/mobile/scraps/transactions/${id}/reverse`, { method: 'POST', data: trackedWriteData(data) }),
  planDrawings: (params = {}) => request('/api/mobile/plans/drawings', { data: params }),
  planMatch: (params = {}) => request('/api/mobile/plans/match', { data: params }),
  rawPlateSpecifications: (params = {}) => request('/api/mobile/raw-plate-specifications', { data: params }),
  createRawPlateSpecification: (data) => request('/api/mobile/raw-plate-specifications', { method: 'POST', data: trackedWriteData(data) }),
  updateRawPlateSpecification: (id, data) => request(`/api/mobile/raw-plate-specifications/${id}`, { method: 'PUT', data: trackedWriteData(data) }),
  toggleRawPlateSpecification: (id, data) => request(`/api/mobile/raw-plate-specifications/${id}/toggle`, { method: 'POST', data: trackedWriteData(data) }),
  rawPlates: (params = {}) => request('/api/mobile/raw-plates', { data: params }),
  rawPlateBatch: (id) => request(`/api/mobile/raw-plates/${id}`),
  updateRawPlateBatch: (id, data) => request(`/api/mobile/raw-plates/${id}`, { method: 'PUT', data: trackedWriteData(data) }),
  rawPlateInbound: (data) => request('/api/mobile/raw-plates/inbound', { method: 'POST', data: trackedWriteData(data) }),
  rawPlateOutbound: (data) => request('/api/mobile/raw-plates/outbound', { method: 'POST', data: trackedWriteData(data) }),
  rawPlateTransactions: (params = {}) => request('/api/mobile/raw-plates/transactions', { data: params }),
  reverseRawPlateTransaction: (id, data) => request(`/api/mobile/raw-plates/transactions/${id}/reverse`, { method: 'POST', data: trackedWriteData(data) }),
  paperSpecifications: (params = {}) => request('/api/mobile/paper-specifications', { data: params }),
  createPaperSpecification: (data) => request('/api/mobile/paper-specifications', { method: 'POST', data: trackedWriteData(data) }),
  updatePaperSpecification: (id, data) => request(`/api/mobile/paper-specifications/${id}`, { method: 'PUT', data: trackedWriteData(data) }),
  togglePaperSpecification: (id, data) => request(`/api/mobile/paper-specifications/${id}/toggle`, { method: 'POST', data: trackedWriteData(data) }),
  paperMaterials: (params = {}) => request('/api/mobile/paper-materials', { data: params }),
  paperBatches: (specificationId, params = {}) => request(`/api/mobile/paper-materials/${specificationId}/batches`, { data: params }),
  paperInbound: (data) => request('/api/mobile/paper-materials/inbound', { method: 'POST', data: trackedWriteData(data) }),
  paperOutbound: (data) => request('/api/mobile/paper-materials/outbound', { method: 'POST', data: trackedWriteData(data) }),
  paperTransactions: (params = {}) => request('/api/mobile/paper-materials/transactions', { data: params }),
  reversePaperTransaction: (id, data) => request(`/api/mobile/paper-materials/transactions/${id}/reverse`, { method: 'POST', data: trackedWriteData(data) })
}
