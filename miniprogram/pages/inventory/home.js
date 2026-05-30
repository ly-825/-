const api = require('../../utils/api')

Page({
  data: {
    productQuantity: 0
  },

  onShow() {
    this.load()
  },

  async load() {
    try {
      const summary = await api.summary()
      this.setData({ productQuantity: summary.product_available_quantity || 0 })
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    }
  },

  go(event) {
    wx.navigateTo({ url: event.currentTarget.dataset.url })
  }
})
