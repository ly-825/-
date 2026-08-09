const api = require('../../utils/api')

Page({
  data: { filters: { material: '', thickness: '', required_diameter: '', location: '' }, items: [], loading: false, error: '' },
  onShow() { this.load() },
  onFilter(event) { this.setData({ [`filters.${event.currentTarget.dataset.field}`]: event.detail.value }) },
  async load() {
    if (this.data.loading) return
    this.setData({ loading: true, error: '' })
    try {
      this.setData({ items: await api.scraps(this.data.filters) })
    } catch (error) {
      this.setData({ error: error.message || '余料库存加载失败' })
    } finally {
      this.setData({ loading: false })
    }
  },
  openDetail(event) {
    const item = this.data.items[event.currentTarget.dataset.index]
    if (!item || !item.group_key) return
    wx.setStorageSync('scrap-detail', item)
    wx.navigateTo({ url: '/pages/scraps/detail' })
  }
})
