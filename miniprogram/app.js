const connection = require('./utils/connection')
const auth = require('./utils/auth')
const releaseConfig = require('./release-config')

App({
  globalData: {
    baseUrl: '',
    connectionState: 'unknown',
    authenticated: false,
    account: null,
    envVersion: 'develop',
    canEditConnection: true,
    connectionError: ''
  },

  onLaunch() {
    let envVersion = 'develop'
    try {
      envVersion = wx.getAccountInfoSync().miniProgram.envVersion || 'develop'
    } catch (error) {
      envVersion = 'develop'
    }
    this.globalData.envVersion = envVersion
    this.globalData.canEditConnection = connection.canEditConnection(envVersion)
    this.globalData.authenticated = auth.hasSession(wx)
    this.globalData.account = auth.loadAccount(wx)
    try {
      this.globalData.baseUrl = connection.baseUrlForEnvironment(envVersion, {
        releaseBaseUrl: releaseConfig.releaseBaseUrl,
        wxApi: wx
      })
      this.globalData.connectionError = ''
    } catch (error) {
      this.globalData.baseUrl = ''
      this.globalData.connectionError = error.message
    }
  }
})
