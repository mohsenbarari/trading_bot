import type { DirectiveBinding } from 'vue'

export const vRipple = {
    mounted(el: HTMLElement, binding: DirectiveBinding) {
        el.classList.add('ripple-container')

        el.addEventListener('mousedown', (e) => {
            const rect = el.getBoundingClientRect()

            const x = e.clientX - rect.left
            const y = e.clientY - rect.top

            const circle = document.createElement('span')
            const diameter = Math.max(rect.width, rect.height)
            const radius = diameter / 2

            circle.style.width = circle.style.height = `${diameter}px`
            circle.style.left = `${x - radius}px`
            circle.style.top = `${y - radius}px`
            circle.classList.add('ripple-effect')

            // Optional: Customizable ripple color via binding value
            if (binding.value) {
                circle.style.backgroundColor = binding.value
            }

            const rippleElements = el.getElementsByClassName('ripple-effect')
            Array.from(rippleElements).forEach(ripple => ripple.remove())

            el.appendChild(circle)

            setTimeout(() => {
                circle.remove()
            }, 600) // Match the CSS animation duration
        })
    }
}
