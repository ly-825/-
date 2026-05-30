const api = require('../../utils/api')

Page({
  data: {
    pendingCount: 0,
    confirmedCount: 0
  },

  onShow() {
    this.load()
  },

  async load() {
    try {
      const summary = await api.summary()
      this.setData({
        pendingCount: summary.pending_drawing_count || 0,
        confirmedCount: summary.confirmed_drawing_count || 0
      })
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    }
  },

  go(event) {
    wx.navigateTo({ url: event.currentTarget.dataset.url })
  }
})
