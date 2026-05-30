const api = require('../../utils/api')

Page({
  data: { filters: { material: '', thickness: '', required_diameter: '', location: '' }, items: [] },
  onShow() { this.load() },
  onFilter(event) { this.setData({ [`filters.${event.currentTarget.dataset.field}`]: event.detail.value }) },
  async load() {
    try {
      this.setData({ items: await api.scraps(this.data.filters) })
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    }
  }
})
