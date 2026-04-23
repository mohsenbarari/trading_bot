// Shared IntersectionObserver for chat media hydration.
//
// Each `ChatMessageItem.vue` previously instantiated its own
// `IntersectionObserver` to decide when to trigger media hydration.
// In a chat with ~100 media messages this meant ~100 live observers,
// each with its own thread-level callback scheduling inside the
// browser's intersection engine.
//
// This module exposes a singleton observer keyed by rootMargin +
// threshold. All `ChatMessageItem` instances share one observer,
// so the browser only maintains one intersection calculation pipeline
// for chat media, dramatically reducing background cost on weak phones.
//
// API:
//   observeVisibility(element, onVisible) -> unobserve()
//
// - `onVisible` fires once when the element enters the rootMargin
//   window and the returned unobserve() stops watching immediately.
// - Callers are responsible for calling the returned unobserve()
//   on unmount (or on explicit cleanup) to avoid leaks.

const VISIBILITY_ROOT_MARGIN = '900px 0px 900px 0px'
const VISIBILITY_THRESHOLD = 0.01

type VisibilityCallback = () => void

let sharedObserver: IntersectionObserver | null = null
const callbackByTarget = new WeakMap<Element, VisibilityCallback>()

function getSharedObserver(): IntersectionObserver | null {
    if (sharedObserver) return sharedObserver
    if (typeof IntersectionObserver === 'undefined') return null

    sharedObserver = new IntersectionObserver((entries) => {
        for (const entry of entries) {
            const isVisible = entry.isIntersecting || entry.intersectionRatio > 0
            if (!isVisible) continue
            const cb = callbackByTarget.get(entry.target)
            if (!cb) continue
            // One-shot: unobserve first, then fire. This prevents
            // double-fires if the callback triggers a synchronous
            // layout change that keeps the target intersecting.
            callbackByTarget.delete(entry.target)
            sharedObserver?.unobserve(entry.target)
            try {
                cb()
            } catch (e) {
                // Swallow: hydration failure in one cell must not stop
                // other cells from receiving their visibility signal.
                console.error('[sharedVisibilityObserver] callback failed', e)
            }
        }
    }, {
        threshold: VISIBILITY_THRESHOLD,
        rootMargin: VISIBILITY_ROOT_MARGIN
    })

    return sharedObserver
}

/**
 * Start watching `element`. `onVisible` fires at most once the first
 * time the element enters the visibility window. Returns an unobserve
 * function that is safe to call multiple times.
 *
 * If IntersectionObserver is unavailable, `onVisible` is scheduled
 * on the next task (setTimeout 0) as a fallback, matching the
 * previous per-component behavior.
 */
export function observeVisibility(element: Element, onVisible: VisibilityCallback): () => void {
    const observer = getSharedObserver()
    if (!observer) {
        const id = window.setTimeout(() => {
            try { onVisible() } catch (e) { console.error(e) }
        }, 0)
        return () => window.clearTimeout(id)
    }

    callbackByTarget.set(element, onVisible)
    observer.observe(element)

    return () => {
        if (callbackByTarget.has(element)) {
            callbackByTarget.delete(element)
            observer.unobserve(element)
        }
    }
}
