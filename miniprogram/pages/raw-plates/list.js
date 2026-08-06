const api = require('../../utils/api')

function presentGroup(group) {
  return {
    ...group,
    locations_text: (group.locations || []).join(' / ') || '-',
  }
}

Page({
  data: { items: [], q: '', loading: false, error: '' },

  onShow() {
    this.load()
  },

  onInput(event) {
    this.setData({ q: event.detail.value })
  },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      const groups = await api.rawPlates({ q: this.data.q })
      this.setData({ items: groups.map(presentGroup) })
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ loading: false })
    }
  },

  detail(event) {
    wx.setStorageSync(
      'raw-plate-group',
      this.data.items[event.currentTarget.dataset.index],
    )
    wx.navigateTo({ url: '/pages/raw-plates/detail' })
  },
})
