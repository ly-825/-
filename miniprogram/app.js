const connection = require('./utils/connection')

App({
  globalData: {
    baseUrl: '',
    connectionState: 'unknown'
  },

  onLaunch() {
    try {
      this.globalData.baseUrl = connection.loadSavedBaseUrl(wx)
    } catch (error) {
      this.globalData.baseUrl = ''
    }
  }
})
