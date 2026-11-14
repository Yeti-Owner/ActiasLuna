import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-3 whitespace-nowrap rounded-xl text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#d1e6b3] focus-visible:ring-offset-4 focus-visible:ring-offset-[#12301d] disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-[#ecf1de] text-[#163521] hover:bg-white",
        outline: "border border-white/25 bg-white/5 text-white hover:border-white/45 hover:bg-white/10",
        secondary: "bg-white/10 text-white/85 hover:bg-white/15",
        ghost: "text-white/80 hover:bg-white/10",
      },
      size: { default: "h-11 px-5", sm: "h-9 px-3", lg: "h-12 px-6", icon: "h-10 w-10" },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
)

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return <Comp ref={ref} className={cn(buttonVariants({ variant, size, className }))} {...props} />
  }
)
Button.displayName = "Button"
export { Button, buttonVariants }
