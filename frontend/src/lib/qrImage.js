import jsQR from 'jsqr'

// Decode a QR code from an image file (a saved/screenshotted QR). Same jsQR
// decoder as the live camera scan -- no extra dependency. Resolves to the QR
// text, or null when the image holds no readable QR code.
export async function decodeQrFromFile(file) {
  const bitmap = await createImageBitmap(file)
  try {
    const canvas = document.createElement('canvas')
    // Cap the working size: a 12-megapixel photo is slow to scan and needs no more detail.
    const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height))
    canvas.width = Math.round(bitmap.width * scale)
    canvas.height = Math.round(bitmap.height * scale)
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    ctx.fillStyle = '#fff' // transparent PNG pixels would otherwise decode as black
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    const frame = ctx.getImageData(0, 0, canvas.width, canvas.height)
    return jsQR(frame.data, frame.width, frame.height)?.data ?? null
  } finally {
    bitmap.close?.()
  }
}
