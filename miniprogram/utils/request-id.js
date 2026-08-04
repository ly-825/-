function createRequestId(nowFn = Date.now, randomFn = Math.random) {
  const randomPart = Math.floor(randomFn() * 0x100000000)
    .toString(16)
    .padStart(8, '0')
  return `mobile-${nowFn()}-${randomPart}`
}

function canonicalize(value) {
  if (Array.isArray(value)) {
    return value.map(canonicalize)
  }
  if (value && typeof value === 'object') {
    return Object.keys(value)
      .filter((key) => key !== 'client_request_id')
      .sort()
      .reduce((result, key) => {
        result[key] = canonicalize(value[key])
        return result
      }, {})
  }
  return value
}

class PendingRequestError extends Error {
  constructor() {
    super('上一次库存操作结果尚未确认，请先重试上次操作')
    this.name = 'PendingRequestError'
    this.code = 'PENDING_REQUEST_UNRESOLVED'
  }
}

function createPendingRequestTracker(operationKey, storage = wx, createId = createRequestId) {
  const storageKey = `pending-mobile-write:${operationKey}`

  function read() {
    try {
      return storage.getStorageSync(storageKey) || null
    } catch (error) {
      return null
    }
  }

  function write(value) {
    storage.setStorageSync(storageKey, value)
  }

  return {
    withRequestId(data = {}) {
      const payload = canonicalize(data)
      const fingerprint = JSON.stringify(payload)
      const pending = read()
      if (pending && pending.fingerprint !== fingerprint) {
        throw new PendingRequestError()
      }
      const requestId = pending ? pending.client_request_id : createId()
      if (!pending) {
        write({ client_request_id: requestId, fingerprint, payload })
      }
      return { ...data, client_request_id: requestId }
    },
    retryPending() {
      const pending = read()
      if (!pending || !pending.payload || !pending.client_request_id) {
        throw new Error('没有可重试的库存操作')
      }
      return { ...pending.payload, client_request_id: pending.client_request_id }
    },
    complete() {
      storage.removeStorageSync(storageKey)
    }
  }
}

module.exports = {
  createRequestId,
  createPendingRequestTracker,
  PendingRequestError
}
