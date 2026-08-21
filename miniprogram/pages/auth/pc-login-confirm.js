const api = require('../../utils/api')
const pcLogin = require('../../utils/pc-login')

Page({
  data: {
    requestToken: '',
    summary: null,
    loading: true,
    submitting: false,
    error: ''
  },

  async onLoad(options) {
    const requestToken = String(options.request_token || '')
    this.setData({ requestToken })
    try {
      const summary = await pcLogin.readPcLogin(api.request, requestToken)
      this.setData({ summary })
    } catch (error) {
      this.setData({ error: error.message || '登录请求读取失败' })
    } finally {
      this.setData({ loading: false })
    }
  },

  async confirm() {
    return this.submitDecision({ approved: true })
  },

  async deny() {
    return this.submitDecision({ approved: false })
  },

  async submitDecision({ approved }) {
    if (this.data.submitting) return
    this.setData({ submitting: true, error: '' })
    try {
      await pcLogin.decidePcLogin(api.request, this.data.requestToken, approved)
      wx.showToast({ title: approved ? '已确认登录' : '已拒绝登录', icon: 'success' })
      setTimeout(() => wx.navigateBack(), 500)
    } catch (error) {
      this.setData({ error: error.message || '操作失败，请重试', submitting: false })
    }
  }
})
