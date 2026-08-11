const api = require('../../utils/api')
const auth = require('../../utils/auth')
const connection = require('../../utils/connection')

Page({
  data: {
    username: '',
    activationCode: '',
    loading: false,
    error: ''
  },

  onShow() {
    const app = getApp()
    let baseUrl = app.globalData.baseUrl
    if (!baseUrl && app.globalData.canEditConnection) {
      try {
        baseUrl = connection.loadSavedBaseUrl(wx)
      } catch (error) {
        baseUrl = ''
      }
    }
    if (!baseUrl) {
      wx.reLaunch({ url: '/pages/connection/index' })
      return
    }
    api.configureBaseUrl(baseUrl)
    if (auth.hasSession(wx)) {
      this.openBusinessHome()
    }
  },

  onUsername(event) {
    this.setData({ username: event.detail.value })
  },

  onActivationCode(event) {
    this.setData({ activationCode: event.detail.value })
  },

  async wechatLogin() {
    this.setData({ loading: true, error: '' })
    try {
      await auth.login(wx, api.request)
      this.openBusinessHome()
    } catch (error) {
      this.setData({ error: error.message || '尚未绑定，请使用工号和激活码完成首次绑定' })
    } finally {
      this.setData({ loading: false })
    }
  },

  async activate() {
    const username = this.data.username.trim()
    const activationCode = this.data.activationCode.trim()
    if (!username || !/^\d{8}$/.test(activationCode)) {
      this.setData({ error: '请输入工号和 8 位激活码' })
      return
    }
    this.setData({ loading: true, error: '' })
    try {
      await auth.activate(wx, api.request, username, activationCode)
      this.openBusinessHome()
    } catch (error) {
      this.setData({ error: error.message || '绑定失败，请核对工号和激活码' })
    } finally {
      this.setData({ loading: false })
    }
  },

  openBusinessHome() {
    getApp().globalData.authenticated = true
    wx.switchTab({ url: '/pages/plan/home' })
  }
})
