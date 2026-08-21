const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')
const { currentOperatorName } = require('../../utils/operator')

const tracker = createPendingRequestTracker('paper-inbound')

Page({
  data: {
    specs: [],
    selected: null,
    operatorName: '',
    loading: false,
    error: '',
    confirmOpen: false,
    confirmLines: [],
    submitting: false,
    form: {
      batch_code: '',
      quantity: '',
      unit_price: '',
      location: '',
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
      const specs = await api.paperSpecifications()
      this.setData({ specs: specs.filter((specification) => specification.is_active) })
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ loading: false })
    }
  },

  select(event) {
    this.setData({ selected: this.data.specs[event.detail.value] })
  },

  input(event) {
    this.setData({
      [`form.${event.currentTarget.dataset.field}`]: event.detail.value,
    })
  },

  submit() {
    const selected = this.data.selected
    const f = this.data.form
    const price = Number(f.unit_price)
    if (
      !selected
      || !Number.isInteger(Number(f.quantity))
      || Number(f.quantity) <= 0
      || !String(f.unit_price).trim()
      || !Number.isFinite(price)
      || price < 0
    ) {
      wx.showToast({ title: '请选择规格并填写有效数量和单价', icon: 'none' })
      return
    }
    this.setData({
      confirmOpen: true,
      confirmLines: [
        { label: '规格', value: selected.model },
        { label: '数量', value: `${f.quantity} ${selected.unit}` },
        { label: '单价', value: `¥${f.unit_price}` },
        { label: '库位', value: f.location || '-' },
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
          specification_id: selected.id,
          ...f,
          quantity: Number(f.quantity),
        },
        '纸材入库',
      )
      if (!pending) return
      await api.paperInbound(pending)
      tracker.complete()
      wx.navigateBack()
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ submitting: false, confirmOpen: false })
    }
  },
})
