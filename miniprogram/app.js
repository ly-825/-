const connection = require('./utils/connection')
const auth = require('./utils/auth')

App({
  globalData: {
    baseUrl: '',
    connectionState: 'unknown',
    authenticated: false
  },

  onLaunch() {
    try {
      this.globalData.baseUrl = connection.loadSavedBaseUrl(wx)
      this.globalData.authenticated = auth.hasSession(wx)
    } catch (error) {
      this.globalData.baseUrl = ''
    }
  }
})
