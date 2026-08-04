const api = require('../../utils/api')

Page({
  data: {
    pendingCount: 0,
    availableQuantity: 0,
    loading: false,
    error: ''
  },

  onShow() {
    this.load()
  },

  async load() {
    if (this.data.loading) return
    this.setData({ loading: true, error: '' })
    try {
      const summary = await api.summary()
      this.setData({
        pendingCount: summary.pending_scrap_count || 0,
        availableQuantity: summary.scrap_available_quantity || 0
      })
    } catch (error) {
      this.setData({ error: error.message || '余料概览加载失败' })
    } finally {
      this.setData({ loading: false })
    }
  },

  go(event) {
    wx.navigateTo({ url: event.currentTarget.dataset.url })
  }
})
