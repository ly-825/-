const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')

const tracker = createPendingRequestTracker('raw-plate-spec-toggle')

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
      this.setData({ items: await api.rawPlateSpecifications({ q: this.data.q }) })
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ loading: false })
    }
  },

  create() {
    wx.removeStorageSync('raw-plate-spec-edit')
    wx.navigateTo({ url: '/pages/raw-plates/specification-form' })
  },

  edit(event) {
    wx.setStorageSync(
      'raw-plate-spec-edit',
      this.data.items[event.currentTarget.dataset.index],
    )
    wx.navigateTo({ url: '/pages/raw-plates/specification-form?id=1' })
  },

  toggle(event) {
    const item = this.data.items[event.currentTarget.dataset.index]
    wx.showModal({
      title: item.is_active ? '停用规格' : '启用规格',
      content: item.spec_name,
      success: async (result) => {
        if (!result.confirm) return
        try {
          const pending = await retryPendingWrite(
            tracker,
            { specification_id: item.id },
            '启停钢板规格',
          )
          if (!pending) return
          const specificationId = pending.specification_id
          await api.toggleRawPlateSpecification(specificationId, pending)
          tracker.complete()
          this.load()
        } catch (error) {
          this.setData({ error: error.message })
        }
      },
    })
  },
})
