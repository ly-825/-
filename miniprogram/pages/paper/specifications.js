const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')

const tracker = createPendingRequestTracker('paper-spec-toggle')

Page({
  data: { items: [], q: '', loading: false, error: '' },

  onShow() {
    this.load()
  },

  input(event) {
    this.setData({ q: event.detail.value })
  },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      this.setData({ items: await api.paperSpecifications({ q: this.data.q }) })
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ loading: false })
    }
  },

  create() {
    wx.removeStorageSync('paper-spec-edit')
    wx.navigateTo({ url: '/pages/paper/specification-form' })
  },

  edit(event) {
    wx.setStorageSync(
      'paper-spec-edit',
      this.data.items[event.currentTarget.dataset.index],
    )
    wx.navigateTo({ url: '/pages/paper/specification-form?id=1' })
  },

  toggle(event) {
    const specification = this.data.items[event.currentTarget.dataset.index]
    wx.showModal({
      title: specification.is_active ? '停用规格' : '启用规格',
      content: specification.model,
      success: async (result) => {
        if (!result.confirm) return
        try {
          const pending = await retryPendingWrite(
            tracker,
            { specification_id: specification.id },
            '启停纸材规格',
          )
          if (!pending) return
          const specificationId = pending.specification_id
          await api.togglePaperSpecification(specificationId, pending)
          tracker.complete()
          this.load()
        } catch (error) {
          this.setData({ error: error.message })
        }
      },
    })
  },
})
