import { ShaderBackground } from "./waves-header-green"

export function SiteBackground() {
  return (
    <div className="site-background" aria-hidden="true">
      <ShaderBackground className="absolute inset-0" />
      <div className="site-background-shade" />
    </div>
  )
}
