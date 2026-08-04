const api = require('../../utils/api')

Page({
  data: {
    loading: true,
    error: '',
    pendingScrapCount: 0,
    baseUrl: ''
  },

  onShow() {
    if (getApp().globalData.connectionState !== 'connected') {
      wx.reLaunch({ url: '/pages/connection/index' })
      return
    }
    this.setData({ baseUrl: getApp().globalData.baseUrl })
    this.load()
  },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      const summary = await api.summary()
      this.setData({ pendingScrapCount: summary.pending_scrap_count || 0 })
    } catch (error) {
      this.setData({ error: error.message || '材料概览加载失败' })
    } finally {
      this.setData({ loading: false })
    }
  },

  go(event) {
    wx.navigateTo({ url: event.currentTarget.dataset.url })
  },

  configure() {
    wx.reLaunch({ url: '/pages/connection/index' })
  }
})
