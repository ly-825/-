Page({
  data: { baseUrl: '' },
  onShow() {
    if (getApp().globalData.connectionState !== 'connected') {
      wx.reLaunch({ url: '/pages/connection/index' })
      return
    }
    this.setData({ baseUrl: getApp().globalData.baseUrl })
  },
  configure() {
    wx.reLaunch({ url: '/pages/connection/index' })
  },

  open(event) {
    wx.navigateTo({ url: `/pages/raw-plates/${event.currentTarget.dataset.page}` })
  }
})
