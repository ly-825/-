const api = require('../../utils/api')
const auth = require('../../utils/auth')
const pcLogin = require('../../utils/pc-login')

const ROLE_LABELS = {
  superadmin: '主管理员',
  owner: '老板',
  employee: '员工'
}

Page({
  data: {
    account: {},
    roleLabel: '',
    canApprovePcLogin: false,
    loading: false,
    error: ''
  },

  onShow() {
    const account = auth.loadAccount(wx)
    if (!account || !ROLE_LABELS[account.role]) {
      auth.handleUnauthorized(wx)
      return
    }
    this.setData({
      account,
      roleLabel: ROLE_LABELS[account.role],
      canApprovePcLogin: auth.canApprovePcLogin(account.role)
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

  async logout() {
    try {
      await auth.logout(wx, api.request)
    } finally {
      getApp().globalData.authenticated = false
      getApp().globalData.account = null
      wx.reLaunch({ url: '/pages/auth/login' })
    }
  }
})
