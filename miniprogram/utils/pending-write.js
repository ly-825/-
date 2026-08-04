function retryPendingWrite(tracker, currentPayload, operationLabel, modal = wx.showModal) {
  try {
    return Promise.resolve(tracker.withRequestId(currentPayload))
  } catch (error) {
    if (error.code !== 'PENDING_REQUEST_UNRESOLVED') {
      return Promise.reject(error)
    }
  }

  return new Promise((resolve, reject) => {
    modal({
      title: '先核对上次操作',
      content: `${operationLabel}存在一笔结果不确定的请求。本次填写内容与上次不同，请先重试上次操作；系统会返回原结果，不会重复增减库存。`,
      confirmText: '重试上次',
      cancelText: '暂不操作',
      success(result) {
        if (!result.confirm) {
          resolve(null)
          return
        }
        try {
          resolve(tracker.retryPending())
        } catch (error) {
          reject(error)
        }
      },
      fail: reject
    })
  })
}

module.exports = { retryPendingWrite }
