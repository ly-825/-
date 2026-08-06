const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')

const tracker = createPendingRequestTracker('paper-outbound')

Page({
  data: {
    items: [],
    selected: null,
    loading: false,
    error: '',
    confirmOpen: false,
    confirmLines: [],
    submitting: false,
    form: {
      quantity: '',
      location: '',
      customer_name: '',
      operator_name: '',
      remark: '',
    },
  },

  onShow() {
    this.load()
  },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      this.setData({ items: await api.paperMaterials() })
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ loading: false })
    }
  },

  select(event) {
    this.setData({ selected: this.data.items[event.currentTarget.dataset.index] })
  },

  input(event) {
    this.setData({
      [`form.${event.currentTarget.dataset.field}`]: event.detail.value,
    })
  },

  submit() {
    const selected = this.data.selected
    const f = this.data.form
    if (
      !selected
      || !Number.isInteger(Number(f.quantity))
      || Number(f.quantity) <= 0
    ) {
      wx.showToast({ title: '请选择规格并填写有效数量', icon: 'none' })
      return
    }
    this.setData({
      confirmOpen: true,
      confirmLines: [
        { label: '规格', value: selected.model },
        { label: '数量', value: `${f.quantity} ${selected.unit}` },
        { label: '库位', value: f.location || '全部库位 FIFO' },
        { label: '客户/去向', value: f.customer_name || '-' },
      ],
    })
  },

  cancelConfirm() {
    this.setData({ confirmOpen: false })
  },

  async confirmSubmit() {
    this.setData({ submitting: true })
    try {
      const selected = this.data.selected
      const f = this.data.form
      const pending = await retryPendingWrite(
        tracker,
        {
          specification_id: selected.specification_id,
          quantity: Number(f.quantity),
          location: f.location,
          customer_name: f.customer_name,
          operator_name: f.operator_name,
          remark: f.remark,
        },
        '纸材出库',
      )
      if (!pending) return
      await api.paperOutbound(pending)
      tracker.complete()
      this.setData({ selected: null, confirmOpen: false })
      this.load()
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ submitting: false })
    }
  },
})
