Page({
  data: { baseUrl: '', menus: [{page:'specifications',title:'规格',desc:'维护纸圈、纸张规格'},{page:'list',title:'库存',desc:'查看库存汇总和批次'},{page:'inbound',title:'入库',desc:'记录批次、数量和单价'},{page:'outbound',title:'出库',desc:'按规格 FIFO 出库'},{page:'transactions',title:'流水',desc:'查看并撤销出入库'}] },
  onShow() {
    if (getApp().globalData.connectionState !== 'connected') {
      wx.reLaunch({ url: '/pages/connection/index' })
      return
    }
    this.setData({ baseUrl: getApp().globalData.baseUrl })
  },
  configure() {
    wx.reLaunch({ url: '/pages/connection/index' })
  },

  open(event) {
    wx.navigateTo({ url: `/pages/paper/${event.currentTarget.dataset.page}` })
  }
})
