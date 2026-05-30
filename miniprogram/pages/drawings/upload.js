const api = require('../../utils/api')

Page({
  data: { filePath: '', fileName: '', loading: false },

  chooseFile() {
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['dxf'],
      success: (res) => {
        const file = res.tempFiles[0]
        this.setData({ filePath: file.path, fileName: file.name })
      }
    })
  },

  async upload() {
    if (!this.data.filePath) {
      wx.showToast({ title: '请先选择文件', icon: 'none' })
      return
    }
    this.setData({ loading: true })
    try {
      const result = await api.uploadDrawing(this.data.filePath)
      const drawing = result.drawing || result
      if (result.duplicated) {
        wx.showModal({
          title: '图纸已存在',
          content: '系统识别到这是已上传过的 DXF，将打开已有图纸详情，不会重复新增。',
          showCancel: false,
          confirmText: '查看详情',
          success: () => {
            wx.navigateTo({ url: `/pages/drawings/detail?id=${drawing.id}` })
          }
        })
        return
      }
      wx.showToast({ title: '上传成功', icon: 'success' })
      wx.navigateTo({ url: `/pages/drawings/detail?id=${drawing.id}` })
    } catch (error) {
      wx.showToast({ title: error.message || '上传失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  }
})
