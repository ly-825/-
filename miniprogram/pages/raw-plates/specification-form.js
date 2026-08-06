const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')

const tracker = createPendingRequestTracker('raw-plate-spec-save')

Page({
  data: {
    id: null,
    loading: false,
    error: '',
    confirmOpen: false,
    submitting: false,
    confirmLines: [],
    form: {
      material: '',
      length: '',
      width: '',
      thickness: '',
      density: '7.85',
      remark: '',
      is_active: 1,
    },
  },

  onLoad(options) {
    const specification = wx.getStorageSync('raw-plate-spec-edit')
    if (options.id && specification) {
      this.setData({
        id: specification.id,
        form: {
          material: specification.material,
          length: specification.length,
          width: specification.width,
          thickness: specification.thickness,
          density: specification.density,
          remark: specification.remark || '',
          is_active: specification.is_active,
        },
      })
    }
  },

  onShow() {},

  onInput(event) {
    this.setData({
      [`form.${event.currentTarget.dataset.field}`]: event.detail.value,
    })
  },

  validateForm(form) {
    return Boolean(
      form.material.trim()
      && Number(form.length) > 0
      && Number(form.width) > 0
      && Number(form.thickness) > 0
      && Number(form.density) > 0
    )
  },

  submit() {
    const form = this.data.form
    if (!this.validateForm(form)) {
      wx.showToast({ title: '请完整填写材质、尺寸和密度', icon: 'none' })
      return
    }
    this.setData({
      confirmOpen: true,
      confirmLines: [
        { label: '材质', value: form.material },
        { label: '尺寸', value: `${form.thickness}×${form.width}×${form.length}` },
        { label: '密度', value: String(form.density) },
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
          length: Number(form.length),
          width: Number(form.width),
          thickness: Number(form.thickness),
          density: Number(form.density),
          is_active: Number(form.is_active),
        },
        '保存钢板规格',
      )
      if (!pending) return
      const specificationId = pending.specification_id
      if (specificationId) {
        await api.updateRawPlateSpecification(specificationId, pending)
      } else {
        await api.createRawPlateSpecification(pending)
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
