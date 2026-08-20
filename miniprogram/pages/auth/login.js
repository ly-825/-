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
      this.openBusinessHome(auth.loadAccount(wx))
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
      const result = await auth.login(wx, api.request)
      this.openBusinessHome(result.account)
    } catch (error) {
      this.setData({ error: error.message || '尚未绑定，请使用账号和激活码完成首次绑定' })
    } finally {
      this.setData({ loading: false })
    }
  },

  async activate() {
    const username = this.data.username.trim()
    const activationCode = this.data.activationCode.trim()
    if (!username || !/^\d{8}$/.test(activationCode)) {
      this.setData({ error: '请输入账号和 8 位激活码' })
      return
    }
    this.setData({ loading: true, error: '' })
    try {
      const result = await auth.activate(wx, api.request, username, activationCode)
      this.openBusinessHome(result.account)
    } catch (error) {
      this.setData({ error: error.message || '绑定失败，请核对账号和激活码' })
    } finally {
      this.setData({ loading: false })
    }
  },

  openBusinessHome(account) {
    const app = getApp()
    app.globalData.authenticated = true
    app.globalData.account = account || auth.loadAccount(wx)
    const url = auth.homeForRole(app.globalData.account && app.globalData.account.role)
    if (url === '/pages/plan/home') {
      wx.switchTab({ url })
      return
    }
    wx.reLaunch({ url })
  }
})
