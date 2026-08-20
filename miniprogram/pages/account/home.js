const api = require('../../utils/api')
const auth = require('../../utils/auth')
const pcLogin = require('../../utils/pc-login')

Page({
  data: { account: {}, roleLabel: '', loading: false, error: '' },

  onShow() {
    const account = auth.loadAccount(wx)
    if (!account || !['owner', 'superadmin'].includes(account.role)) {
      auth.handleUnauthorized(wx)
      return
    }
    this.setData({
      account,
      roleLabel: account.role === 'superadmin' ? '主管理员' : '老板'
    })
  },

  async scanPcLogin() {
    this.setData({ loading: true, error: '' })
    try {
      const result = await pcLogin.scanPcLogin(wx, api.request)
      wx.navigateTo({
        url: `/pages/auth/pc-login-confirm?request_token=${encodeURIComponent(result.requestToken)}`
      })
    } catch (error) {
      this.setData({ error: error.message || '扫码失败，请重试' })
    } finally {
      this.setData({ loading: false })
    }
  },

  logout() {
    auth.clearSession(wx)
    getApp().globalData.authenticated = false
    getApp().globalData.account = null
    wx.reLaunch({ url: '/pages/auth/login' })
  }
})
