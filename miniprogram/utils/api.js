const app = getApp()

function baseUrl() {
  return app.globalData.baseUrl.replace(/\/$/, '')
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
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${baseUrl()}${path}`,
      method: options.method || 'GET',
      data: options.data || {},
      header: {
        'content-type': 'application/json'
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
          return
        }
        reject(new Error(errorMessage(res.data, '请求失败')))
      },
      fail(error) {
        reject(error)
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
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          try {
            resolve(JSON.parse(res.data))
          } catch (error) {
            resolve(res.data)
          }
          return
        }
        reject(new Error(errorMessage(res.data, '上传失败')))
      },
      fail(error) {
        reject(error)
      }
    })
  })
}

module.exports = {
  request,
  uploadFile,
  summary: () => request('/api/mobile/summary'),
  drawings: (params = {}) => request('/api/mobile/drawings', { data: params }),
  pendingDrawings: () => request('/api/mobile/drawings/pending'),
  confirmedDrawings: (q = '') => request('/api/mobile/drawings/confirmed', { data: { q } }),
  drawingDetail: (id) => request(`/api/mobile/drawings/${id}`),
  deleteDrawing: (id) => request(`/api/mobile/drawings/${id}`, { method: 'DELETE' }),
  confirmDrawing: (id, data) => request(`/api/mobile/drawings/${id}/confirm`, { method: 'POST', data }),
  rerunDrawing: (id) => request(`/api/mobile/drawings/${id}/rerun`, { method: 'POST' }),
  uploadDrawing: (filePath) => uploadFile('/api/mobile/drawings/upload', filePath),
  products: (params = {}) => request('/api/mobile/products', { data: params }),
  productBatches: (productCode) => request(`/api/mobile/products/${encodeURIComponent(productCode)}/batches`),
  productInbound: (data) => request('/api/mobile/products/inbound', { method: 'POST', data }),
  productOutbound: (data) => request('/api/mobile/products/outbound', { method: 'POST', data }),
  productTransactions: () => request('/api/mobile/products/transactions'),
  reverseProductTransaction: (id, data = {}) => request(`/api/mobile/products/transactions/${id}/reverse`, { method: 'POST', data }),
  pendingScraps: () => request('/api/mobile/scraps/pending'),
  confirmScrap: (id, data) => request(`/api/mobile/scraps/${id}/confirm`, { method: 'POST', data }),
  scraps: (params = {}) => request('/api/mobile/scraps', { data: params }),
  scrapOutbound: (data) => request('/api/mobile/scraps/outbound', { method: 'POST', data }),
  scrapTransactions: () => request('/api/mobile/scraps/transactions'),
  reverseScrapTransaction: (id, data = {}) => request(`/api/mobile/scraps/transactions/${id}/reverse`, { method: 'POST', data })
}
