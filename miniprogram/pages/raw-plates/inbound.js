const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')
const { currentOperatorName } = require('../../utils/operator')

const tracker = createPendingRequestTracker('raw-plate-inbound')

Page({
  data: {
    specs: [],
    operatorName: '',
    loading: false,
    error: '',
    confirmOpen: false,
    confirmLines: [],
    submitting: false,
    form: { specification_id: null, total_weight_ton: '', location: '', material_code: '', remark: '' },
  },
  onShow() {
    this.setData({ operatorName: currentOperatorName(wx) })
    this.load()
  },
  async load() {
    this.setData({ loading: true, error: '' })
    try {
      this.setData({ specs: (await api.rawPlateSpecifications()).filter((item) => item.is_active) })
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ loading: false })
    }
  },
  select(event) {
    this.setData({ 'form.specification_id': this.data.specs[event.detail.value].id })
  },
  input(event) {
    this.setData({ [`form.${event.currentTarget.dataset.field}`]: event.detail.value })
  },
  submit() {
    const form = this.data.form
    const specification = this.data.specs.find((item) => item.id === form.specification_id)
    if (!specification || Number(form.total_weight_ton) <= 0) {
      wx.showToast({ title: '请选择规格并填写重量', icon: 'none' })
      return
    }
    this.setData({
      confirmOpen: true,
      confirmLines: [
        { label: '规格', value: specification.spec_name },
        { label: '总重量', value: `${form.total_weight_ton} 吨` },
        { label: '库位', value: form.location || '-' },
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
      const pending = await retryPendingWrite(tracker, {
        ...this.data.form,
        total_weight_ton: Number(this.data.form.total_weight_ton),
      }, '钢板入库')
      if (!pending) return
      const result = await api.rawPlateInbound(pending)
      tracker.complete()
      this.setData({ confirmOpen: false })
      wx.showModal({ title: '入库成功', content: `入库 ${result.quantity} 块，余重 ${Number(result.remaining_weight_kg).toFixed(3)}kg`, showCancel: false })
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ submitting: false })
    }
  },
})
