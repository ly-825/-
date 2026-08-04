const api = require('../../utils/api')
const connection = require('../../utils/connection')

Page({
  data: {
    state: 'checking',
    baseUrl: '',
    ip: '',
    port: '8000',
    errorTitle: '',
    errorDescription: '',
    manualOpen: false
  },

  onShow() {
    this.trySavedConnection()
  },

  async testConnection(baseUrl) {
    api.configureBaseUrl(baseUrl)
    const health = await api.health()
    if (!health || health.status !== 'ok') {
      throw new Error('后台健康检查未通过')
    }
    connection.saveBaseUrl(wx, baseUrl)
    getApp().globalData.connectionState = 'connected'
    wx.switchTab({ url: '/pages/plan/home' })
  },

  async trySavedConnection() {
    let saved = ''
    try {
      saved = connection.loadSavedBaseUrl(wx)
    } catch (error) {
      saved = ''
    }
    if (!saved) {
      this.setData({ state: 'setup' })
      return
    }
    this.setData({ state: 'checking', baseUrl: saved })
    try {
      await this.testConnection(saved)
    } catch (error) {
      getApp().globalData.connectionState = 'error'
      this.setData({
        state: 'error',
        errorTitle: '无法连接厂内库存系统',
        errorDescription: '请连接工厂 Wi-Fi，并确认后台电脑已经启动。'
      })
    }
  },

  retrySavedConnection() {
    this.setData({ manualOpen: false })
    this.trySavedConnection()
  },

  async scanBaseUrl() {
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
    this.setData({ manualOpen: true, state: 'setup' })
  },

  onIp(event) {
    this.setData({ ip: event.detail.value })
  },

  onPort(event) {
    this.setData({ port: event.detail.value })
  },

  async testAndSave() {
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
