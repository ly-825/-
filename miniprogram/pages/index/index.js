const api = require('../../utils/api')

Page({
  data: {
    summary: {
      pending_drawing_count: 0,
      pending_scrap_count: 0
    }
  },

  onShow() {
    this.loadSummary()
  },

  async loadSummary() {
    try {
      const summary = await api.summary()
      this.setData({
        summary: {
          pending_drawing_count: summary.pending_drawing_count || 0,
          pending_scrap_count: summary.pending_scrap_count || 0
        }
      })
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    }
  },

  go(event) {
    wx.navigateTo({ url: event.currentTarget.dataset.url })
  }
})
