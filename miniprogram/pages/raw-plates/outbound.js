const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')
const { currentOperatorName } = require('../../utils/operator')

const tracker = createPendingRequestTracker('raw-plate-outbound')

function presentGroup(group) {
  return {
    ...group,
    locations_text: (group.locations || []).join(' / ') || '-',
  }
}

Page({
  data: {
    items: [],
    selected: null,
    operatorName: '',
    loading: false,
    error: '',
    confirmOpen: false,
    confirmLines: [],
    submitting: false,
    form: {
      quantity: '',
      location: '',
      customer_name: '',
      remark: '',
    },
  },

  onShow() {
    this.setData({ operatorName: currentOperatorName(wx) })
    this.load()
  },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      const groups = await api.rawPlates()
      this.setData({ items: groups.map(presentGroup) })
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
        { label: '规格', value: selected.spec_name },
        { label: '出库数量', value: `${f.quantity} 块` },
        { label: '指定库位', value: f.location || '全部库位 FIFO' },
        { label: '客户/去向', value: f.customer_name || '-' },
        { label: '操作人', value: this.data.operatorName || '当前账号' },
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
          material: selected.material,
          length: selected.length,
          width: selected.width,
          thickness: selected.thickness,
          quantity: Number(f.quantity),
          location: f.location,
          customer_name: f.customer_name,
          remark: f.remark,
        },
        '钢板出库',
      )
      if (!pending) return
      await api.rawPlateOutbound(pending)
      tracker.complete()
      this.setData({ confirmOpen: false, selected: null })
      this.load()
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ submitting: false })
    }
  },
})
