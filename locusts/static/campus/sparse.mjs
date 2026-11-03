// Map-sized layers that are mostly empty (the breeze frames, occluders.png, the lit windows), kept as just their
// used tiles packed into one atlas canvas, so each holds a few MB instead of the whole map's ~84 MB of pixels.

// A grid of `tile`-pixel tiles over a width x height layer, some of which have a place in the atlas.
class Tiles {
  constructor(width, height, tile) {
    this.width = width;
    this.height = height;
    this.tile = tile;
    this.tw = Math.ceil(width / tile);
    this.slot = new Int32Array(this.tw * Math.ceil(height / tile)).fill(-1);
    this.count = 0;
  }

  // Gives tile (tx, ty) the next place in the atlas, if it has none yet; returns its place.
  claim(tx, ty) {
    const k = ty * this.tw + tx;
    if (this.slot[k] < 0) this.slot[k] = this.count++;
    return this.slot[k];
  }

  makeAtlas(count) {
    this.cols = Math.floor(2048 / this.tile);
    const rows = Math.max(1, Math.ceil(count / this.cols));
    this.atlas = Object.assign(document.createElement("canvas"), { width: this.cols * this.tile, height: rows * this.tile });
    this.ctx = this.atlas.getContext("2d");
  }

  // Where place i sits in the atlas.
  at(i) {
    return [(i % this.cols) * this.tile, Math.floor(i / this.cols) * this.tile];
  }

  // Each tile's part of the whole-pixel rect (x, y, w, h), as [place, x, y, w, h] in layer pixels.
  *parts(x, y, w, h) {
    const T = this.tile, x1 = Math.min(this.width, x + w), y1 = Math.min(this.height, y + h);
    x = Math.max(0, x);
    y = Math.max(0, y);
    for (let ty = Math.floor(y / T); ty * T < y1; ty++) {
      for (let tx = Math.floor(x / T); tx * T < x1; tx++) {
        const i = this.slot[ty * this.tw + tx];
        if (i < 0) continue;
        const ix = Math.max(x, tx * T), iy = Math.max(y, ty * T);
        yield [i, ix, iy, Math.min(x1, (tx + 1) * T) - ix, Math.min(y1, (ty + 1) * T) - iy, tx * T, ty * T];
      }
    }
  }

  // Draws the layer's (x, y, w, h) region at the same spot on ctx, as drawImage(layer, x, y, w, h, x, y, w, h)
  // would. Coordinates are whole pixels.
  draw(ctx, x, y, w, h) {
    for (const [i, ix, iy, iw, ih, ox, oy] of this.parts(x, y, w, h)) {
      const [ax, ay] = this.at(i);
      ctx.drawImage(this.atlas, ax + ix - ox, ay + iy - oy, iw, ih, ix, iy, iw, ih);
    }
  }
}

const IMAGE_TILE = 32;

// A decoded image, keeping only its tiles with any painted pixel.
export class SparseImage extends Tiles {
  // Decodes `src`, keeps its painted tiles, and lets the full image go. With `mask`, also keeps which pixels are
  // painted, for opaque().
  static async load(src, { mask = false } = {}) {
    const image = Object.assign(new Image(), { src });
    await image.decode();
    return new SparseImage(image, { mask });
  }

  constructor(image, { mask = false } = {}) {
    super(image.width, image.height, IMAGE_TILE);
    const T = IMAGE_TILE, { width } = image, th = Math.ceil(image.height / T);
    // Read the image a strip of tiles at a time, so finding the painted ones never needs a full-size copy, and
    // copy the tiles out of the strip, which lives in main memory: drawing straight from the image would upload
    // all of it to the GPU, where Chrome keeps it cached after the image is gone.
    const strip = Object.assign(document.createElement("canvas"), { width, height: T })
      .getContext("2d", { willReadFrequently: true });
    const read = (ty) => {
      strip.clearRect(0, 0, width, T);
      strip.drawImage(image, 0, ty * T, width, T, 0, 0, width, T);
      return strip.getImageData(0, 0, width, T).data;
    };
    const painted = (data, tx) => {
      for (let y = 0; y < T; y++) {
        for (let x = 0; x < T && tx * T + x < width; x++) if (data[(y * width + tx * T + x) * 4 + 3]) return true;
      }
      return false;
    };
    let count = 0;
    for (let ty = 0; ty < th; ty++) {
      const data = read(ty);
      for (let tx = 0; tx < this.tw; tx++) count += painted(data, tx);
    }
    this.makeAtlas(count);
    this.masks = mask ? [] : null;
    const tile = new ImageData(T, T);
    for (let ty = 0; ty < th; ty++) {
      const data = read(ty);
      for (let tx = 0; tx < this.tw; tx++) {
        if (!painted(data, tx)) continue;
        const i = this.claim(tx, ty), n = Math.min(T, width - tx * T);
        const bits = mask ? new Uint8Array((T * T) / 8) : null;
        for (let y = 0; y < T; y++) {
          const from = (y * width + tx * T) * 4;
          tile.data.fill(0, y * T * 4, (y + 1) * T * 4);
          tile.data.set(data.subarray(from, from + n * 4), y * T * 4);
          if (bits) for (let x = 0; x < n; x++) if (data[from + x * 4 + 3]) bits[(y * T + x) >> 3] |= 0x80 >> ((y * T + x) & 7);
        }
        if (mask) this.masks[i] = bits;
        this.ctx.putImageData(tile, ...this.at(i));
      }
    }
    strip.canvas.width = strip.canvas.height = 0;
  }

  // Whether layer pixel (x, y) is painted (needs `mask` at load). Off the image counts as painted.
  opaque(x, y) {
    if (x < 0 || y < 0 || x >= this.width || y >= this.height) return true;
    const T = IMAGE_TILE, tx = Math.floor(x / T), ty = Math.floor(y / T);
    const i = this.slot[ty * this.tw + tx];
    if (i < 0) return false;
    const k = (y - ty * T) * T + (x - tx * T);
    return (this.masks[i][k >> 3] & (0x80 >> (k & 7))) !== 0;
  }
}

// A blank layer that's only ever painted inside `rects` ([x, y, w, h] each), with room for just their tiles.
// Bigger tiles than an image's, since it's drawn every frame at night and fewer tiles mean fewer draws.
export class SparseCanvas extends Tiles {
  constructor(width, height, rects, tile = 64) {
    super(width, height, tile);
    for (const [x, y, w, h] of rects) {
      for (let ty = Math.floor(y / tile); ty * tile < y + h; ty++) {
        for (let tx = Math.floor(x / tile); tx * tile < x + w; tx++) this.claim(tx, ty);
      }
    }
    this.makeAtlas(this.count);
  }

  // Draws src's (sx, sy, w, h) at layer pixel (x, y) with composite operation `op`.
  paint(src, sx, sy, w, h, x, y, op = "source-over") {
    this.ctx.globalCompositeOperation = op;
    for (const [i, ix, iy, iw, ih, ox, oy] of this.parts(x, y, w, h)) {
      const [ax, ay] = this.at(i);
      this.ctx.drawImage(src, sx + ix - x, sy + iy - y, iw, ih, ax + ix - ox, ay + iy - oy, iw, ih);
    }
  }
}
