export interface MothSprite { left: number; right: number; height: number; overlap: number }
export declare const SPRITES: { watercolor: MothSprite; luna: MothSprite }

export interface MothFlyOptions {
  /** Which sprite geometry to use: a preset name or explicit sizes. Default "watercolor". */
  sprite?: "watercolor" | "luna" | MothSprite
  /** Folder holding moth-left.png and moth-right.png. Default "/static/moth/". */
  src?: string
  /** Wingspan in px on a normal screen. Default 150. */
  width?: number
  /** Gap kept between the moth and content, px. Default 20. */
  margin?: number
  /** Viewport position (0..1) at the top of the page. Default 0.14. */
  top?: number
  /** Viewport position (0..1) at the bottom of the page. Default 0.78. */
  bottom?: number
  /** Soft light edge so dark wings read on dark backgrounds. Default true. */
  glow?: boolean
  zIndex?: number
  /** CSS selector for elements the moth must avoid. */
  avoid?: string
  /** CSS selector for regions the moth may fly over. Default "[data-moth-ignore]". */
  ignore?: string
  /** Optional { left, right } image URLs; overrides src. */
  images?: { left: string; right: string } | null
}
/** Starts the moth. Returns a function that removes it. */
export function startMoth(options?: MothFlyOptions): () => void
export default startMoth
