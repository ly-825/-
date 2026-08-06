const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')

const tracker = createPendingRequestTracker('paper-spec-save')

Page({
  data: {
    id: null,
    loading: false,
    error: '',
    confirmOpen: false,
    confirmLines: [],
    submitting: false,
    form: {
      paper_type: 'roll',
      model: '',
      material_name: '',
      thickness: '',
      inner_diameter: '',
      outer_diameter: '',
      length: '',
      width: '',
      remark: '',
      is_active: 1,
    },
  },

  onLoad(options) {
    const specification = wx.getStorageSync('paper-spec-edit')
    if (options.id && specification) {
      this.setData({
        id: specification.id,
        form: { ...specification, remark: specification.remark || '' },
      })
    }
  },

  onShow() {},

  type(event) {
    this.setData({ 'form.paper_type': event.currentTarget.dataset.type })
  },

  input(event) {
    this.setData({
      [`form.${event.currentTarget.dataset.field}`]: event.detail.value,
    })
  },

  validateForm(form) {
    if (!form.material_name.trim() || Number(form.thickness) <= 0) return false
    if (form.paper_type === 'roll') {
      return Boolean(
        form.model.trim()
        && Number(form.inner_diameter) > 0
        && Number(form.outer_diameter) > Number(form.inner_diameter)
      )
    }
    return Number(form.length) > 0 && Number(form.width) > 0
  },

  submit() {
    const form = this.data.form
    if (!this.validateForm(form)) {
      wx.showToast({ title: '请完整填写有效的纸材规格', icon: 'none' })
      return
    }
    this.setData({
      confirmOpen: true,
      confirmLines: [
        { label: '类型', value: form.paper_type === 'roll' ? '纸圈' : '纸张' },
        { label: '型号', value: form.paper_type === 'roll' ? form.model : '系统按尺寸生成' },
        { label: '材质', value: form.material_name },
        { label: '厚度', value: String(form.thickness) },
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
          specification_id: this.data.id,
          ...form,
          thickness: Number(form.thickness),
          inner_diameter: form.inner_diameter ? Number(form.inner_diameter) : null,
          outer_diameter: form.outer_diameter ? Number(form.outer_diameter) : null,
          length: form.length ? Number(form.length) : null,
          width: form.width ? Number(form.width) : null,
          is_active: Number(form.is_active),
        },
        '保存纸材规格',
      )
      if (!pending) return
      const specificationId = pending.specification_id
      if (specificationId) {
        await api.updatePaperSpecification(specificationId, pending)
      } else {
        await api.createPaperSpecification(pending)
      }
      tracker.complete()
      wx.navigateBack()
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ submitting: false, confirmOpen: false })
    }
  },
})
