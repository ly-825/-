const api = require('../../utils/api')
const auth = require('../../utils/auth')
const connection = require('../../utils/connection')

Page({
  data: {
    state: 'checking',
    baseUrl: '',
    ip: '',
    port: '8000',
    errorTitle: '',
    errorDescription: '',
    manualOpen: false,
    canEdit: true
  },

  onShow() {
    const app = getApp()
    this.setData({ canEdit: app.globalData.canEditConnection !== false })
    this.trySavedConnection()
  },

  async testConnection(baseUrl) {
    api.configureBaseUrl(baseUrl)
    const health = await api.health()
    if (!health || health.status !== 'ok') {
      throw new Error('后台健康检查未通过')
    }
    const app = getApp()
    if (this.data.canEdit) {
      connection.saveBaseUrl(wx, baseUrl)
    }
    app.globalData.baseUrl = baseUrl
    app.globalData.connectionState = 'connected'
    if (auth.hasSession(wx)) {
      wx.switchTab({ url: '/pages/plan/home' })
      return
    }
    wx.reLaunch({ url: '/pages/auth/login' })
  },

  async trySavedConnection() {
    const app = getApp()
    let saved = app.globalData.baseUrl
    if (!saved && this.data.canEdit) {
      try {
        saved = connection.loadSavedBaseUrl(wx)
      } catch (error) {
        saved = ''
      }
    }
    if (!saved) {
      this.setData({
        state: this.data.canEdit ? 'setup' : 'error',
        errorTitle: this.data.canEdit ? '' : '正式版连接地址未配置',
        errorDescription: this.data.canEdit
          ? ''
          : app.globalData.connectionError || '请使用个人 API 域名重新构建小程序'
      })
      return
    }
    this.setData({ state: 'checking', baseUrl: saved })
    try {
      await this.testConnection(saved)
    } catch (error) {
      getApp().globalData.connectionState = 'error'
      this.setData({
        state: 'error',
        errorTitle: this.data.canEdit ? '无法连接厂内库存系统' : '无法连接云端库存系统',
        errorDescription: this.data.canEdit
          ? '请连接工厂 Wi-Fi，并确认后台电脑已经启动。'
          : '请检查网络，或联系管理员检查云端服务。'
      })
    }
  },

  retrySavedConnection() {
    this.setData({ manualOpen: false })
    this.trySavedConnection()
  },

  async scanBaseUrl() {
    if (!this.data.canEdit) return
    this.setData({ state: 'checking' })
    try {
      const baseUrl = await connection.scanBaseUrl(wx)
      await this.testConnection(baseUrl)
    } catch (error) {
      getApp().globalData.connectionState = 'error'
      this.setData({
        state: 'error',
        errorTitle: '连接二维码无效',
        errorDescription: error.message || '请在电脑后台重新生成二维码'
      })
    }
  },

  openManual() {
    if (!this.data.canEdit) return
    this.setData({ manualOpen: true, state: 'setup' })
  },

  onIp(event) {
    this.setData({ ip: event.detail.value })
  },

  onPort(event) {
    this.setData({ port: event.detail.value })
  },

  async testAndSave() {
    if (!this.data.canEdit) return
    try {
      const baseUrl = connection.normalizeBaseUrl(
        `http://${this.data.ip}:${this.data.port}`
      )
      this.setData({ state: 'checking', baseUrl })
      await this.testConnection(baseUrl)
    } catch (error) {
      getApp().globalData.connectionState = 'error'
      this.setData({
        state: 'error',
        manualOpen: true,
        errorTitle: '手工地址无法连接',
        errorDescription: error.message || '请检查 IP 和端口'
      })
    }
  }
})
