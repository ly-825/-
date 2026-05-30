const api = require('../../utils/api')

Page({
  data: { filters: { q: '', material: '', thickness: '' }, items: [] },

  onShow() { this.load() },

  onFilter(event) {
    this.setData({ [`filters.${event.currentTarget.dataset.field}`]: event.detail.value })
  },

  async load() {
    try {
      const items = (await api.products(this.data.filters)).map((item) => ({
        ...item,
        material_text: item.material || '-',
        thickness_text: item.thickness || '-',
        location_text: item.locations && item.locations.length ? item.locations.join(' / ') : '-'
      }))
      this.setData({ items })
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    }
  }
})
