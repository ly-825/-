const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')

const tracker = createPendingRequestTracker('raw-plate-batch-update')

Page({
  data: {
    group: {},
    items: [],
    form: null,
    loading: false,
    error: '',
    confirmOpen: false,
    confirmLines: [],
    submitting: false,
  },

  onShow() {
    this.load()
  },

  async load() {
    const group = wx.getStorageSync('raw-plate-group') || {}
    this.setData({ group, loading: true, error: '' })
    try {
      const items = await Promise.all(
        (group.batch_ids || []).map((id) => api.rawPlateBatch(id)),
      )
      this.setData({ items })
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ loading: false })
    }
  },

  edit(event) {
    this.setData({ form: { ...this.data.items[event.currentTarget.dataset.index] } })
  },

  onInput(event) {
    this.setData({
      [`form.${event.currentTarget.dataset.field}`]: event.detail.value,
    })
  },

  submit() {
    const form = this.data.form
    this.setData({
      confirmOpen: true,
      confirmLines: [
        { label: '批次', value: form.material_code || '-' },
        { label: '型号', value: form.raw_plate_model },
        { label: '库位', value: form.location || '-' },
      ],
    })
  },

  cancelConfirm() {
    this.setData({ confirmOpen: false })
  },

  async confirmSubmit() {
    this.setData({ submitting: true })
    try {
      const form = this.data.form
      const pending = await retryPendingWrite(
        tracker,
        {
          batch_id: form.id,
          raw_plate_model: form.raw_plate_model,
          material_code: form.material_code || '',
          material: form.material,
          length: Number(form.length),
          width: Number(form.width),
          thickness: Number(form.thickness),
          location: form.location || '',
          status: form.status,
          operator_name: form.operator_name || '',
          remark: form.remark || '',
        },
        '修改钢板批次',
      )
      if (!pending) return
      const batchId = pending.batch_id
      await api.updateRawPlateBatch(batchId, pending)
      tracker.complete()
      this.setData({ form: null, confirmOpen: false })
      this.load()
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ submitting: false })
    }
  },
})
